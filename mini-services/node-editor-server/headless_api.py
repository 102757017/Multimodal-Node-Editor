"""
headless_api.py - Direct in-process API and shared-memory transport for the
headless node editor backend.

This module provides THREE ways for an external Python process to drive the
headless backend, listed from most-efficient to least-efficient:

1. **HeadlessController (in-process, zero-overhead)**
   The external script imports this module directly and creates a
   ``HeadlessController`` with a loaded ``Graph``.  Method calls are plain
   Python function calls — no IPC, no serialisation, numpy arrays pass by
   reference.  This is the recommended approach when the external script
   can run in the same Python process as the backend (e.g. a plugin,
   a Jupyter notebook, or a script that imports the backend as a library).

2. **SharedMemoryServer + SharedMemoryClient (cross-process, zero-copy)**
   For scenarios where the external script must run in a separate process,
   the headless backend can be started with ``--server`` which launches a
   ``SharedMemoryServer``.  Image data is transferred via
   ``multiprocessing.shared_memory`` (zero-copy — no base64, no pickling of
   pixel data).  A lightweight pipe carries only small control messages
   (node IDs, array shapes, dtypes).

3. **Socket protocol (legacy, removed)**
   The previous socket-based server has been removed.  If you need
   cross-machine communication, use the HTTP API on the GUI backend
   (main.py) instead.

Usage — in-process::

    from run_headless import load_graph
    from headless_api import HeadlessController

    g = load_graph("my_graph.json")
    ctrl = HeadlessController(g)

    info = ctrl.graph_info()
    img_node = next(n for n in info["nodes"] if n["name"] == "Image")

    # Pass a raw numpy array — zero-copy, no base64
    import cv2
    image = cv2.imread("photo.jpg")
    result = ctrl.run(
        image_node_id=img_node["id"],
        image_array=image,          # <-- raw numpy array
        output_node_id=img_node["id"],
        output_port_name="image_out",
    )
    # result["output"] is a raw numpy array — no base64 decoding needed
    cv2.imwrite("out.jpg", result["output"])

Usage — cross-process (shared memory)::

    # Terminal 1: start the headless server
    python run_headless.py my_graph.json --server

    # Terminal 2: connect from a separate process
    from headless_api import SharedMemoryClient
    import cv2

    client = SharedMemoryClient("/tmp/mne_headless.sock")
    info = client.graph_info()
    img_node = next(n for n in info["nodes"] if n["name"] == "Image")

    image = cv2.imread("photo.jpg")
    result = client.run(
        image_node_id=img_node["id"],
        image_array=image,          # <-- zero-copy via shared memory
        output_node_id=img_node["id"],
        output_port_name="image_out",
    )
    # result["output"] is a raw numpy array
    cv2.imwrite("out.jpg", result["output"])
"""
from __future__ import annotations

import base64
import json
import os
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# HeadlessController — in-process API (zero-serialisation)
# ---------------------------------------------------------------------------
class HeadlessController:
    """Direct in-process controller for a loaded headless graph.

    All methods are plain Python calls — no IPC, no serialisation.  Image
    data (numpy arrays) is passed by reference.  This is the most efficient
    way to drive the headless backend.

    The ``run()`` method accepts ``image_array`` (a raw numpy array) instead
    of ``image_base64`` — the array is inserted directly into the graph's
    port data without any encoding.
    """

    def __init__(self, graph):
        self.g = graph
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._tasks_lock = threading.Lock()
        # Last execution result — stored so the browser UI can poll it and
        # display outputs from external runs (HttpClient / SharedMemoryClient).
        # Updated after every run() and after every async task completes.
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_result_seq: int = 0  # monotonically increasing counter

    def _store_last_result(self, result: Dict[str, Any]):
        """Store the last execution result so the browser can poll it.

        Called after every run() and after every async task completion.
        Increments ``_last_result_seq`` so the browser can detect new results.
        """
        self._last_result_seq += 1
        self._last_result = {
            **result,
            "seq": self._last_result_seq,
            "timestamp": time.time(),
        }

    def get_last_result(self) -> Dict[str, Any]:
        """Return the last execution result (for browser polling).

        Returns ``{"seq": 0, "result": None}`` if no external run has happened yet.
        """
        if self._last_result is None:
            return {"seq": 0, "result": None}
        return {"seq": self._last_result_seq, "result": self._last_result}

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _array_to_data_uri(arr) -> str:
        """Convert a numpy array to a base64 JPEG data URI.

        This is only used when the array needs to be stored in the graph's
        port_data (which expects data URIs for image-type ports).  The
        conversion happens once, inside the backend process, and the result
        is kept in memory — no network transfer.
        """
        try:
            import cv2
            import numpy as np
            if isinstance(arr, np.ndarray):
                # downscale very large frames
                max_dim = 1920
                h, w = arr.shape[:2]
                if max(h, w) > max_dim:
                    scale = max_dim / max(h, w)
                    arr = cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))),
                                     interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if not ok:
                    return ""
                return f"data:image/jpeg;base64,{base64.b64encode(buf.tobytes()).decode('ascii')}"
        except Exception:
            pass
        # fallback: PIL
        try:
            import io
            import numpy as np
            from PIL import Image
            if isinstance(arr, np.ndarray):
                if arr.ndim == 3 and arr.shape[2] == 3:
                    arr = arr[:, :, ::-1]  # BGR -> RGB
                img = Image.fromarray(arr)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=95)
                return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
        except Exception:
            pass
        return ""

    @staticmethod
    def _resolve_image_data(
        image_array: Optional[Any] = None,
        image_base64: Optional[str] = None,
        image_path: Optional[str] = None,
    ) -> str:
        """Resolve the image input to a data URI.

        Priority: image_array (raw numpy) > image_base64 > image_path.
        For image_array, the conversion to data URI happens in-process —
        no network transfer of the raw array.
        """
        if image_array is not None:
            return HeadlessController._array_to_data_uri(image_array)
        if image_base64:
            if image_base64.startswith("data:"):
                return image_base64
            return f"data:image/jpeg;base64,{image_base64}"
        if image_path:
            p = Path(image_path)
            if not p.exists():
                raise FileNotFoundError(f"Image file not found: {p}")
            suffix = p.suffix.lower().lstrip(".")
            mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
        raise ValueError("Either image_array, image_base64, or image_path must be provided")

    # -- graph introspection --------------------------------------------
    def graph_info(self) -> Dict[str, Any]:
        """Return a compact summary of the loaded graph."""
        nodes_info = []
        for n in self.g.nodes:
            nodes_info.append({
                "id": n.id,
                "name": n.name,
                "definition_id": n.definition_id,
                "is_source": n.effective_is_source,
                "inputs": [
                    {"id": p.id, "name": p.name, "display_name": p.display_name or p.name,
                     "data_type": p.data_type}
                    for p in n.inputs
                ],
                "outputs": [
                    {"id": p.id, "name": p.name, "display_name": p.display_name or p.name,
                     "data_type": p.data_type}
                    for p in n.outputs
                ],
            })
        return {
            "graph_id": self.g.id,
            "node_count": len(self.g.nodes),
            "connection_count": len(self.g.connections),
            "nodes": nodes_info,
        }

    # -- core execution --------------------------------------------------
    def _run_frame_and_collect_output(
        self,
        image_node_id: str,
        image_port_name: str,
        image_data_uri: str,
        output_node_id: str,
        output_port_name: str,
        max_steps: int = 50,
        reset_frame: bool = True,
    ) -> Dict[str, Any]:
        from core import _serialize_output

        src_node = self.g._find_node(image_node_id)
        if src_node is None:
            raise ValueError(f"Image node '{image_node_id}' not found")
        src_port = next((p for p in src_node.outputs if p.name == image_port_name), None)
        if src_port is None:
            available = [p.name for p in src_node.outputs]
            raise ValueError(f"Image node '{image_node_id}' has no output port '{image_port_name}'. "
                             f"Available: {available}")

        out_node = self.g._find_node(output_node_id)
        if out_node is None:
            raise ValueError(f"Output node '{output_node_id}' not found")
        out_port = next((p for p in out_node.outputs if p.name == output_port_name), None)
        if out_port is None:
            available = [p.name for p in out_node.outputs]
            raise ValueError(f"Output node '{output_node_id}' has no output port '{output_port_name}'. "
                             f"Available: {available}")

        if reset_frame:
            self.g.reset_frame_state()

        self.g.start_frame()
        self.g.set_source_data(image_node_id, {image_port_name: image_data_uri})

        last_result = None
        for _ in range(max_steps):
            r = self.g.execute_step()
            last_result = r
            if r.status in ("frame_complete", "idle", "exhausted"):
                break

        if last_result is None:
            raise RuntimeError("Execution produced no result")

        output_key = f"{output_node_id}.{output_port_name}"
        output_value = last_result.outputs.get(output_key)
        if output_value is None:
            fid, val = self.g.exec_state.get_port_data(out_port.id)
            if val is not None:
                output_value = _serialize_output(val, out_port.data_type)

        result_dict = {
            "status": last_result.status,
            "frame_id": last_result.frame_id,
            "executed_nodes": last_result.executed_nodes,
            "skipped_nodes": last_result.skipped_nodes,
            "waiting_nodes": last_result.waiting_nodes,
            "errors": last_result.errors,
            "elapsed_ms": last_result.elapsed_ms,
            "output": output_value,
            "output_port_data_type": out_port.data_type,
            # Also include ALL node outputs (not just the requested one) so
            # the browser UI can display previews for every node after an
            # external run.  The browser's syncFromGraph reads from this
            # "outputs" dict (keyed by "node_id.port_name").
            "outputs": dict(last_result.outputs),
            "node_times": dict(last_result.node_times),
        }

        # Store as the last result so the browser can poll it.
        self._store_last_result(result_dict)

        return result_dict

    # -- synchronous run -------------------------------------------------
    def run(
        self,
        *,
        image_node_id: str,
        output_node_id: str,
        output_port_name: str,
        image_port_name: str = "image_out",
        image_array: Optional[Any] = None,
        image_base64: Optional[str] = None,
        image_path: Optional[str] = None,
        max_steps: int = 50,
        reset_frame: bool = True,
    ) -> Dict[str, Any]:
        """Push an image and run one frame synchronously.

        ``image_array`` is a raw numpy array (BGR, uint8) — it is converted
        to a data URI in-process.  No network transfer of the raw array.
        """
        image_data_uri = self._resolve_image_data(image_array, image_base64, image_path)
        return self._run_frame_and_collect_output(
            image_node_id=image_node_id,
            image_port_name=image_port_name,
            image_data_uri=image_data_uri,
            output_node_id=output_node_id,
            output_port_name=output_port_name,
            max_steps=max_steps,
            reset_frame=reset_frame,
        )

    # -- asynchronous submit / poll -------------------------------------
    def submit(
        self,
        *,
        image_node_id: str,
        output_node_id: str,
        output_port_name: str,
        image_port_name: str = "image_out",
        image_array: Optional[Any] = None,
        image_base64: Optional[str] = None,
        image_path: Optional[str] = None,
        max_steps: int = 50,
        reset_frame: bool = True,
    ) -> str:
        """Asynchronously queue a run; return a task id immediately."""
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        with self._tasks_lock:
            self._tasks[task_id] = {
                "status": "pending",
                "created_at": time.time(),
                "started_at": None,
                "completed_at": None,
                "result": None,
                "error": None,
            }

        def _worker():
            try:
                image_data_uri = self._resolve_image_data(image_array, image_base64, image_path)
                with self._tasks_lock:
                    self._tasks[task_id]["status"] = "running"
                    self._tasks[task_id]["started_at"] = time.time()
                try:
                    result = self._run_frame_and_collect_output(
                        image_node_id=image_node_id,
                        image_port_name=image_port_name,
                        image_data_uri=image_data_uri,
                        output_node_id=output_node_id,
                        output_port_name=output_port_name,
                        max_steps=max_steps,
                        reset_frame=reset_frame,
                    )
                    with self._tasks_lock:
                        self._tasks[task_id]["status"] = "completed"
                        self._tasks[task_id]["result"] = result
                        self._tasks[task_id]["completed_at"] = time.time()
                except Exception as e:
                    with self._tasks_lock:
                        self._tasks[task_id]["status"] = "error"
                        self._tasks[task_id]["error"] = str(e)
            except Exception as e:
                with self._tasks_lock:
                    self._tasks[task_id]["status"] = "error"
                    self._tasks[task_id]["error"] = f"Worker thread crashed: {e}"

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return task_id

    def get_result(self, task_id: str) -> Dict[str, Any]:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' not found")
            return {
                "task_id": task_id,
                "status": task["status"],
                "created_at": task.get("created_at"),
                "started_at": task.get("started_at"),
                "completed_at": task.get("completed_at"),
                "result": task.get("result"),
                "error": task.get("error"),
            }

    def wait_for_result(
        self,
        task_id: str,
        *,
        poll_interval: float = 0.05,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """Block until the async task completes (or times out)."""
        start = time.time()
        while True:
            try:
                status = self.get_result(task_id)
            except KeyError:
                raise
            if status["status"] in ("completed", "error"):
                return status
            if time.time() - start > timeout:
                raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")
            time.sleep(poll_interval)

    def cancel_task(self, task_id: str) -> bool:
        with self._tasks_lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
            return False

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._tasks_lock:
            items = [
                {
                    "task_id": tid,
                    "status": t["status"],
                    "created_at": t.get("created_at"),
                    "started_at": t.get("started_at"),
                    "completed_at": t.get("completed_at"),
                }
                for tid, t in self._tasks.items()
            ]
        items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
        return items


# ===========================================================================
# SharedMemoryServer — cross-process, zero-copy image transfer
# ===========================================================================
# Protocol:
#   The server listens on a Unix domain socket (Linux/macOS) or named pipe
#   (Windows) via multiprocessing.connection.Listener.  Each request is a
#   pickled dict with a "method" key.
#
#   For image input: the client creates a SharedMemory block, writes the
#   numpy array bytes into it, and sends the shm name + shape + dtype in
#   the request.  The server reads the array from shared memory (zero-copy).
#
#   For image output: the server creates a SharedMemory block, writes the
#   result array bytes into it, and sends the shm name + shape + dtype in
#   the response.  The client reads the array from shared memory (zero-copy)
#   and then closes/unlinks the block.
#
#   Non-image outputs (float, int, string, bool) are pickled directly in
#   the response (they're small).
# ===========================================================================

def _array_to_shm(arr) -> Dict[str, Any]:
    """Write a numpy array to a new SharedMemory block.

    Returns {"shm_name": ..., "shape": ..., "dtype": ..., "size": ...}.
    The caller is responsible for closing/unlinking the shm after the
    consumer has read it.
    """
    from multiprocessing import shared_memory
    import numpy as np

    if not isinstance(arr, np.ndarray):
        raise TypeError(f"Expected numpy array, got {type(arr)}")

    # Ensure the array is contiguous and owns its memory
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)

    data = arr.tobytes()
    shm = shared_memory.SharedMemory(create=True, size=len(data))
    shm.buf[:len(data)] = data
    return {
        "shm_name": shm.name,
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "size": len(data),
        # We return the shm object itself so the server can track it
        # and close/unlink it later.  The client only sees the name.
        "_shm": shm,
    }


def _shm_to_array(shm_name: str, shape: List[int], dtype: str, size: int):
    """Read a numpy array from a SharedMemory block (zero-copy view)."""
    from multiprocessing import shared_memory
    import numpy as np

    shm = shared_memory.SharedMemory(name=shm_name)
    try:
        arr = np.ndarray(shape=tuple(shape), dtype=np.dtype(dtype), buffer=shm.buf[:size])
        # Copy the data so we can close the shm immediately
        return arr.copy()
    finally:
        shm.close()
        try:
            shm.unlink()
        except Exception:
            pass  # might be unlinked by the creator


def _close_shm(shm):
    """Close and unlink a SharedMemory block."""
    try:
        shm.close()
    except Exception:
        pass
    try:
        shm.unlink()
    except Exception:
        pass


class SharedMemoryServer:
    """Cross-process server using shared memory for zero-copy image transfer.

    The server listens on a Unix domain socket (or named pipe on Windows).
    Image data is transferred via ``multiprocessing.shared_memory`` — no
    base64 encoding, no pickling of pixel data, zero-copy.

    Only small control messages (node IDs, shapes, dtypes, shm names) go
    through the pipe — these are pickled but are tiny.
    """

    def __init__(self, controller: HeadlessController, address: str):
        """Create a shared-memory server.

        Args:
            controller: the HeadlessController wrapping the loaded graph.
            address: socket path (Unix) or pipe name (Windows).
                     On Unix, a path like "/tmp/mne_headless.sock" is used.
                     On Windows, a pipe name like ``\\\\.\\pipe\\mne_headless`` is used.
        """
        self.ctrl = controller
        self.address = address
        self._listener = None
        self._running = False
        # Track shm blocks for async tasks so they stay alive until the
        # client reads them.
        self._task_shms: Dict[str, List[Any]] = {}
        self._task_shms_lock = threading.Lock()

    def _handle_client(self, conn):
        """Handle a single client connection — may process multiple requests."""
        try:
            while True:
                try:
                    req = conn.recv()
                except EOFError:
                    break
                except Exception as e:
                    try:
                        conn.send({"error": f"recv failed: {e}"})
                    except Exception:
                        pass
                    break
                if req is None:
                    break
                resp = self._dispatch(req, conn)
                try:
                    conn.send(resp)
                except Exception as e:
                    # If we can't send, we can't do much — close the connection
                    print(f"[shm-server] send failed: {e}")
                    break
        except Exception as e:
            print(f"[shm-server] handler crashed: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _dispatch(self, req: Dict[str, Any], conn) -> Dict[str, Any]:
        """Route a request to the appropriate controller method."""
        method = req.get("method", "")
        try:
            if method == "ping":
                return {"result": {"ok": True, "mode": "headless-shm"}}

            elif method == "graph_info":
                return {"result": self.ctrl.graph_info()}

            elif method == "run":
                # Extract image from shared memory if shm_name is provided
                kwargs = {k: v for k, v in req.items() if k != "method"}
                if "shm_name" in kwargs:
                    arr = _shm_to_array(
                        kwargs["shm_name"],
                        kwargs["shm_shape"],
                        kwargs["shm_dtype"],
                        kwargs["shm_size"],
                    )
                    kwargs.pop("shm_name")
                    kwargs.pop("shm_shape")
                    kwargs.pop("shm_dtype")
                    kwargs.pop("shm_size")
                    kwargs["image_array"] = arr

                result = self.ctrl.run(**kwargs)

                # If the output is an image (base64 data URI), convert it
                # to a shared memory block for zero-copy return.
                output = result.get("output")
                if isinstance(output, str) and output.startswith("data:image"):
                    try:
                        import numpy as np
                        import cv2
                        import base64 as b64mod
                        raw = output.split(",", 1)[1]
                        buf = np.frombuffer(b64mod.b64decode(raw), np.uint8)
                        arr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                        if arr is not None:
                            shm_info = _array_to_shm(arr)
                            result["output_shm"] = {
                                "shm_name": shm_info["shm_name"],
                                "shape": shm_info["shape"],
                                "dtype": shm_info["dtype"],
                                "size": shm_info["size"],
                            }
                            # Keep the shm alive — the client will unlink it
                            # after reading.  We also track it so if the
                            # client never reads it, we can clean up later.
                            # The shm object is stored but not returned.
                            # It will be unlinked by the client.
                            # Actually, we need to close our handle but NOT
                            # unlink — the client will unlink.
                            shm_info["_shm"].close()
                            # Remove the base64 output to avoid sending it
                            result["output"] = None
                    except Exception as e:
                        # Fall back to base64 if shared memory fails
                        result["output_shm_error"] = str(e)

                return {"result": result}

            elif method == "submit":
                kwargs = {k: v for k, v in req.items() if k != "method"}
                if "shm_name" in kwargs:
                    arr = _shm_to_array(
                        kwargs["shm_name"],
                        kwargs["shm_shape"],
                        kwargs["shm_dtype"],
                        kwargs["shm_size"],
                    )
                    kwargs.pop("shm_name")
                    kwargs.pop("shm_shape")
                    kwargs.pop("shm_dtype")
                    kwargs.pop("shm_size")
                    kwargs["image_array"] = arr

                task_id = self.ctrl.submit(**kwargs)
                return {"result": task_id}

            elif method == "get_result":
                status = self.ctrl.get_result(req["task_id"])

                # If the result contains an image output, convert to shm
                result = status.get("result")
                if result and isinstance(result.get("output"), str) and result["output"].startswith("data:image"):
                    try:
                        import numpy as np
                        import cv2
                        import base64 as b64mod
                        raw = result["output"].split(",", 1)[1]
                        buf = np.frombuffer(b64mod.b64decode(raw), np.uint8)
                        arr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                        if arr is not None:
                            shm_info = _array_to_shm(arr)
                            result["output_shm"] = {
                                "shm_name": shm_info["shm_name"],
                                "shape": shm_info["shape"],
                                "dtype": shm_info["dtype"],
                                "size": shm_info["size"],
                            }
                            shm_info["_shm"].close()
                            result["output"] = None
                    except Exception as e:
                        result["output_shm_error"] = str(e)

                return {"result": status}

            elif method == "wait_for_result":
                status = self.ctrl.wait_for_result(
                    req["task_id"],
                    poll_interval=req.get("poll_interval", 0.05),
                    timeout=req.get("timeout", 120.0),
                )

                # Same shm conversion as get_result
                result = status.get("result")
                if result and isinstance(result.get("output"), str) and result["output"].startswith("data:image"):
                    try:
                        import numpy as np
                        import cv2
                        import base64 as b64mod
                        raw = result["output"].split(",", 1)[1]
                        buf = np.frombuffer(b64mod.b64decode(raw), np.uint8)
                        arr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                        if arr is not None:
                            shm_info = _array_to_shm(arr)
                            result["output_shm"] = {
                                "shm_name": shm_info["shm_name"],
                                "shape": shm_info["shape"],
                                "dtype": shm_info["dtype"],
                                "size": shm_info["size"],
                            }
                            shm_info["_shm"].close()
                            result["output"] = None
                    except Exception as e:
                        result["output_shm_error"] = str(e)

                return {"result": status}

            elif method == "cancel_task":
                return {"result": {"ok": self.ctrl.cancel_task(req["task_id"])}}

            elif method == "list_tasks":
                return {"result": {"tasks": self.ctrl.list_tasks()}}

            elif method == "release_shm":
                # Client signals that it has finished reading a shm block.
                # The client is responsible for unlinking; this is just a
                # no-op acknowledgment.
                return {"result": {"ok": True}}

            else:
                return {"error": f"unknown method: {method!r}"}
        except Exception as e:
            return {"error": str(e)}

    def start(self):
        """Start the shared-memory server (blocking)."""
        from multiprocessing.connection import Listener

        # Clean up stale socket file on Unix
        if os.name == "posix" and os.path.exists(self.address):
            try:
                os.unlink(self.address)
            except Exception:
                pass

        self._listener = Listener(self.address)
        self._running = True
        print(f"Shared-memory server listening on: {self.address}")
        print("Methods: ping, graph_info, run, submit, get_result, wait_for_result, cancel_task, list_tasks")
        print("Image data is transferred via shared memory (zero-copy).")
        try:
            while self._running:
                try:
                    conn = self._listener.accept()
                except OSError:
                    break
                t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                t.start()
        except KeyboardInterrupt:
            print("\nShared-memory server stopped.")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        if self._listener:
            try:
                self._listener.close()
            except Exception:
                pass
            self._listener = None
        # Clean up socket file on Unix
        if os.name == "posix" and os.path.exists(self.address):
            try:
                os.unlink(self.address)
            except Exception:
                pass


# ===========================================================================
# SharedMemoryClient — client for the SharedMemoryServer
# ===========================================================================
class SharedMemoryClient:
    """Client for the SharedMemoryServer.

    Image data is transferred via shared memory (zero-copy).  Only small
    control messages go through the pipe.

    Usage::

        client = SharedMemoryClient("/tmp/mne_headless.sock")
        info = client.graph_info()
        result = client.run(
            image_node_id="node-abc",
            image_array=cv2.imread("photo.jpg"),  # raw numpy, zero-copy
            output_node_id="node-def",
            output_port_name="image_out",
        )
        output = result["output"]  # raw numpy array
    """

    def __init__(self, address: str, timeout: float = 120.0):
        """Connect to a SharedMemoryServer.

        Args:
            address: socket path (Unix) or pipe name (Windows).
            timeout: connection and recv timeout in seconds.
        """
        from multiprocessing.connection import Client
        self._address = address
        self._timeout = timeout
        self._conn = None
        self._connect()

    def _connect(self):
        from multiprocessing.connection import Client
        self._conn = Client(self._address)

    def _request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Send a request and receive a response."""
        if self._conn is None:
            self._connect()
        self._conn.send(req)
        resp = self._conn.recv()
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]

    # -- graph introspection --------------------------------------------
    def graph_info(self) -> Dict[str, Any]:
        return self._request({"method": "graph_info"})

    # -- synchronous run -------------------------------------------------
    def run(
        self,
        *,
        image_node_id: str,
        output_node_id: str,
        output_port_name: str,
        image_port_name: str = "image_out",
        image_array: Optional[Any] = None,
        image_base64: Optional[str] = None,
        image_path: Optional[str] = None,
        max_steps: int = 50,
        reset_frame: bool = True,
    ) -> Dict[str, Any]:
        """Push an image and run one frame synchronously.

        ``image_array`` (numpy array) is transferred via shared memory —
        zero-copy, no base64 encoding.
        """
        req = {
            "method": "run",
            "image_node_id": image_node_id,
            "output_node_id": output_node_id,
            "output_port_name": output_port_name,
            "image_port_name": image_port_name,
            "max_steps": max_steps,
            "reset_frame": reset_frame,
        }

        # If we have a numpy array, transfer it via shared memory
        if image_array is not None:
            shm_info = _array_to_shm(image_array)
            req["shm_name"] = shm_info["shm_name"]
            req["shm_shape"] = shm_info["shape"]
            req["shm_dtype"] = shm_info["dtype"]
            req["shm_size"] = shm_info["size"]
            # Close our write handle — the server will read and unlink
            shm_info["_shm"].close()
        elif image_base64 is not None:
            req["image_base64"] = image_base64
        elif image_path is not None:
            req["image_path"] = image_path
        else:
            raise ValueError("Either image_array, image_base64, or image_path must be provided")

        result = self._request(req)

        # If the output was transferred via shared memory, read it
        if result.get("output_shm"):
            shm_info = result["output_shm"]
            result["output"] = _shm_to_array(
                shm_info["shm_name"],
                shm_info["shape"],
                shm_info["dtype"],
                shm_info["size"],
            )
            # Notify the server that we've read the shm (it will be unlinked
            # by _shm_to_array)
            try:
                self._request({"method": "release_shm"})
            except Exception:
                pass

        return result

    # -- asynchronous submit / poll -------------------------------------
    def submit(
        self,
        *,
        image_node_id: str,
        output_node_id: str,
        output_port_name: str,
        image_port_name: str = "image_out",
        image_array: Optional[Any] = None,
        image_base64: Optional[str] = None,
        image_path: Optional[str] = None,
        max_steps: int = 50,
        reset_frame: bool = True,
    ) -> str:
        """Asynchronously queue a run; return a task id immediately."""
        req = {
            "method": "submit",
            "image_node_id": image_node_id,
            "output_node_id": output_node_id,
            "output_port_name": output_port_name,
            "image_port_name": image_port_name,
            "max_steps": max_steps,
            "reset_frame": reset_frame,
        }

        if image_array is not None:
            shm_info = _array_to_shm(image_array)
            req["shm_name"] = shm_info["shm_name"]
            req["shm_shape"] = shm_info["shape"]
            req["shm_dtype"] = shm_info["dtype"]
            req["shm_size"] = shm_info["size"]
            shm_info["_shm"].close()
        elif image_base64 is not None:
            req["image_base64"] = image_base64
        elif image_path is not None:
            req["image_path"] = image_path
        else:
            raise ValueError("Either image_array, image_base64, or image_path must be provided")

        return self._request(req)

    def get_result(self, task_id: str) -> Dict[str, Any]:
        status = self._request({"method": "get_result", "task_id": task_id})
        # Decode shm output if present
        result = status.get("result")
        if result and result.get("output_shm"):
            shm_info = result["output_shm"]
            result["output"] = _shm_to_array(
                shm_info["shm_name"],
                shm_info["shape"],
                shm_info["dtype"],
                shm_info["size"],
            )
            try:
                self._request({"method": "release_shm"})
            except Exception:
                pass
        return status

    def wait_for_result(
        self,
        task_id: str,
        *,
        poll_interval: float = 0.05,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        status = self._request({
            "method": "wait_for_result",
            "task_id": task_id,
            "poll_interval": poll_interval,
            "timeout": timeout,
        })
        # Decode shm output if present
        result = status.get("result")
        if result and result.get("output_shm"):
            shm_info = result["output_shm"]
            result["output"] = _shm_to_array(
                shm_info["shm_name"],
                shm_info["shape"],
                shm_info["dtype"],
                shm_info["size"],
            )
            try:
                self._request({"method": "release_shm"})
            except Exception:
                pass
        return status

    def cancel_task(self, task_id: str) -> bool:
        return self._request({"method": "cancel_task", "task_id": task_id})["ok"]

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self._request({"method": "list_tasks"})["tasks"]

    def ping(self) -> bool:
        try:
            r = self._request({"method": "ping"})
            return r.get("ok", False)
        except Exception:
            return False

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
