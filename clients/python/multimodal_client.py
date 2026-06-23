"""multimodal_client — Python client for the Multimodal Node Editor backend.

Provides THREE transport modes, from most-efficient to least-efficient:

1. **DirectClient (in-process, zero-overhead)**
   Imports the backend as a library and calls ``HeadlessController`` directly.
   Numpy arrays pass by reference — no IPC, no serialisation.  Use this when
   the external script can run in the same Python process as the backend.

   URL scheme: ``direct:///path/to/graph.json``

2. **SharedMemoryClient (cross-process, zero-copy)**
   Connects to a headless backend running with ``--server``.  Image data is
   transferred via ``multiprocessing.shared_memory`` (zero-copy).  Only small
   control messages go through a Unix domain socket / named pipe.

   URL scheme: ``shm:///tmp/mne_headless.sock``

3. **HttpClient (cross-machine, HTTP)**
   Connects to the GUI backend's HTTP API.  Image data is base64-encoded.
   Use this only for cross-machine scenarios or when the GUI backend is
   already running.

   URL scheme: ``http://localhost:3030``

Quick start (in-process, most efficient)::

    from multimodal_client import DirectClient
    import cv2

    client = DirectClient("/path/to/mini-services/node-editor-server",
                          graph_path="my_graph.json")
    info = client.graph_info()
    img_node = client.find_node_by_name("Image")

    image = cv2.imread("photo.jpg")
    result = client.run(
        image_node_id=img_node.id,
        image_array=image,          # raw numpy, zero-copy
        output_node_id=img_node.id,
        output_port_name="image_out",
    )
    # result.output is a raw numpy array — no base64!
    cv2.imwrite("out.jpg", result.output)

Quick start (cross-process, shared memory)::

    # Terminal 1:
    python run_headless.py my_graph.json --server

    # Terminal 2:
    from multimodal_client import SharedMemoryClient
    import cv2

    client = SharedMemoryClient("/tmp/mne_headless.sock")
    info = client.graph_info()
    img_node = client.find_node_by_name("Image")

    image = cv2.imread("photo.jpg")
    result = client.run(
        image_node_id=img_node.id,
        image_array=image,          # zero-copy via shared memory
        output_node_id=img_node.id,
        output_port_name="image_out",
    )
    cv2.imwrite("out.jpg", result.output)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Optional asyncio support
# ---------------------------------------------------------------------------
try:
    import asyncio
    import aiohttp
    _ASYNC_AVAILABLE = True
except ImportError:  # pragma: no cover
    asyncio = None
    aiohttp = None
    _ASYNC_AVAILABLE = False


__all__ = [
    "DirectClient",
    "SharedMemoryClient",
    "HttpClient",
    "MultimodalClient",  # alias for HttpClient (backward compat)
    "GraphInfo",
    "NodeInfo",
    "PortInfo",
    "RunResult",
    "TaskStatus",
    "MultimodalError",
]


class MultimodalError(Exception):
    """Raised when the backend returns an error or an invalid response."""


# ---------------------------------------------------------------------------
# Shared data classes
# ---------------------------------------------------------------------------
class RunResult:
    """The result of a synchronous run or a completed async task."""

    def __init__(self, data: Dict[str, Any]):
        self.status: str = data.get("status", "unknown")
        self.frame_id: int = data.get("frame_id", 0)
        self.executed_nodes: List[str] = data.get("executed_nodes", [])
        self.skipped_nodes: List[str] = data.get("skipped_nodes", [])
        self.waiting_nodes: List[str] = data.get("waiting_nodes", [])
        self.errors: Dict[str, str] = data.get("errors", {})
        self.elapsed_ms: float = data.get("elapsed_ms", 0.0)
        self.output: Any = data.get("output")
        self.output_port_data_type: str = data.get("output_port_data_type", "")
        self._raw = data

    @property
    def is_image(self) -> bool:
        """True if the output is a numpy array (DirectClient/SharedMemoryClient)
        or a base64 image data URI (HttpClient)."""
        if self.output is None:
            return False
        # numpy array
        try:
            import numpy as np
            if isinstance(self.output, np.ndarray):
                return True
        except ImportError:
            pass
        # base64 data URI
        return isinstance(self.output, str) and self.output.startswith("data:image")

    def save_output(self, path: Union[str, Path]) -> Path:
        """Save the output to a file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # numpy array — write directly via cv2 or numpy
        try:
            import numpy as np
            if isinstance(self.output, np.ndarray):
                try:
                    import cv2
                    cv2.imwrite(str(p), self.output)
                except ImportError:
                    # fallback: write raw bytes
                    p.write_bytes(self.output.tobytes())
                return p
        except ImportError:
            pass
        # base64 data URI
        if isinstance(self.output, str) and self.output.startswith("data:image"):
            raw = self.output.split(",", 1)[1]
            p.write_bytes(base64.b64decode(raw))
            return p
        # other — write as JSON
        p.write_text(json.dumps(self.output, indent=2, ensure_ascii=False, default=str),
                     encoding="utf-8")
        return p

    def decode_image(self) -> Optional[Any]:
        """If the output is an image, return it as a numpy array or PIL Image."""
        try:
            import numpy as np
            if isinstance(self.output, np.ndarray):
                return self.output
        except ImportError:
            pass
        if isinstance(self.output, str) and self.output.startswith("data:image"):
            raw = self.output.split(",", 1)[1]
            try:
                import numpy as np
                import cv2
                buf = np.frombuffer(base64.b64decode(raw), np.uint8)
                return cv2.imdecode(buf, cv2.IMREAD_COLOR)
            except ImportError:
                pass
            try:
                import io
                from PIL import Image
                return Image.open(io.BytesIO(base64.b64decode(raw)))
            except Exception:
                return None
        return None

    def __repr__(self) -> str:
        return (f"RunResult(status={self.status!r}, frame_id={self.frame_id}, "
                f"elapsed_ms={self.elapsed_ms:.1f}, "
                f"executed={len(self.executed_nodes)}, "
                f"output_type={type(self.output).__name__})")


class TaskStatus:
    """A snapshot of an async task's state."""

    def __init__(self, data: Dict[str, Any]):
        self.task_id: str = data.get("task_id", "")
        self.status: str = data.get("status", "unknown")
        self.created_at: Optional[float] = data.get("created_at")
        self.started_at: Optional[float] = data.get("started_at")
        self.completed_at: Optional[float] = data.get("completed_at")
        self.error: Optional[str] = data.get("error")
        self.result: Optional[RunResult] = None
        if data.get("result") is not None:
            self.result = RunResult(data["result"])

    @property
    def is_done(self) -> bool:
        return self.status in ("completed", "error")

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    def __repr__(self) -> str:
        return f"TaskStatus(task_id={self.task_id!r}, status={self.status!r})"


# ---------------------------------------------------------------------------
# Graph / Node / Port info (shared by all transports)
# ---------------------------------------------------------------------------
class PortInfo:
    def __init__(self, data: Dict[str, Any]):
        self.id: str = data.get("id", "")
        self.name: str = data.get("name", "")
        self.display_name: str = data.get("display_name", self.name)
        self.data_type: str = data.get("data_type", "any")

    def __repr__(self) -> str:
        return f"PortInfo(name={self.name!r}, type={self.data_type!r})"


class NodeInfo:
    def __init__(self, data: Dict[str, Any]):
        self.id: str = data.get("id", "")
        self.name: str = data.get("name", "")
        self.definition_id: str = data.get("definition_id", "")
        self.is_source: bool = data.get("is_source", False)
        self.inputs: List[PortInfo] = [PortInfo(p) for p in data.get("inputs", [])]
        self.outputs: List[PortInfo] = [PortInfo(p) for p in data.get("outputs", [])]

    def find_output(self, name: str) -> Optional[PortInfo]:
        for p in self.outputs:
            if p.name == name:
                return p
        return None

    def __repr__(self) -> str:
        return f"NodeInfo(id={self.id!r}, name={self.name!r}, def={self.definition_id!r})"


class GraphInfo:
    def __init__(self, data: Dict[str, Any]):
        self.graph_id: str = data.get("graph_id", "")
        self.node_count: int = data.get("node_count", 0)
        self.connection_count: int = data.get("connection_count", 0)
        self.nodes: List[NodeInfo] = [NodeInfo(n) for n in data.get("nodes", [])]

    def find_node_by_id(self, node_id: str) -> Optional[NodeInfo]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def find_node_by_name(self, name: str, case_sensitive: bool = False) -> Optional[NodeInfo]:
        if case_sensitive:
            for n in self.nodes:
                if n.name == name:
                    return n
        else:
            lname = name.lower()
            for n in self.nodes:
                if n.name.lower() == lname:
                    return n
        return None

    def find_nodes_by_definition(self, definition_id: str) -> List[NodeInfo]:
        return [n for n in self.nodes if n.definition_id == definition_id]

    def __repr__(self) -> str:
        return (f"GraphInfo(graph_id={self.graph_id!r}, "
                f"nodes={self.node_count}, connections={self.connection_count})")


# ===========================================================================
# DirectClient — in-process, zero-overhead (numpy arrays pass by reference)
# ===========================================================================
class DirectClient:
    """In-process client that imports the backend as a library.

    This is the MOST efficient transport — no IPC, no serialisation.
    Numpy arrays are passed directly to the graph engine and the output
    is returned as a raw numpy array.

    Usage::

        from multimodal_client import DirectClient
        import cv2

        client = DirectClient(
            backend_dir="/path/to/mini-services/node-editor-server",
            graph_path="my_graph.json",
        )
        info = client.graph_info()
        img_node = info.find_node_by_name("Image")

        image = cv2.imread("photo.jpg")
        result = client.run(
            image_node_id=img_node.id,
            image_array=image,
            output_node_id=img_node.id,
            output_port_name="image_out",
        )
        # result.output is a raw numpy array
        cv2.imwrite("out.jpg", result.output)
    """

    def __init__(self, backend_dir: str, graph_path: Optional[str] = None,
                 sync_timeout: float = 5.0):
        """Create an in-process client.

        Args:
            backend_dir: path to the ``mini-services/node-editor-server`` directory.
            graph_path: optional path to a graph.json to load immediately.
            sync_timeout: frame-sync timeout in seconds.
        """
        self.backend_dir = Path(backend_dir).resolve()
        if str(self.backend_dir) not in sys.path:
            sys.path.insert(0, str(self.backend_dir))

        # Import the backend modules
        from run_headless import load_graph, HeadlessController
        self._load_graph = load_graph
        self._HeadlessController = HeadlessController

        self._graph = None
        self._ctrl = None
        self._sync_timeout = sync_timeout

        if graph_path:
            self.load_graph(graph_path)

    def load_graph(self, graph_path: str):
        """Load a graph.json into the in-process backend."""
        from run_headless import load_graph
        self._graph = load_graph(Path(graph_path))
        self._graph.sync_timeout = self._sync_timeout
        self._graph.exec_state.sync_timeout = self._sync_timeout
        self._ctrl = self._HeadlessController(self._graph)

    @property
    def controller(self):
        """Direct access to the underlying HeadlessController."""
        if self._ctrl is None:
            raise MultimodalError("No graph loaded — call load_graph() first")
        return self._ctrl

    def graph_info(self) -> GraphInfo:
        return GraphInfo(self.controller.graph_info())

    def find_node_by_name(self, name: str) -> NodeInfo:
        info = self.graph_info()
        node = info.find_node_by_name(name)
        if node is None:
            raise MultimodalError(f"Node '{name}' not found")
        return node

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
    ) -> RunResult:
        """Push an image and run one frame synchronously.

        ``image_array`` is a raw numpy array — it is passed directly to the
        graph engine without any serialisation.
        """
        result = self.controller.run(
            image_node_id=image_node_id,
            output_node_id=output_node_id,
            output_port_name=output_port_name,
            image_port_name=image_port_name,
            image_array=image_array,
            image_base64=image_base64,
            image_path=image_path,
            max_steps=max_steps,
            reset_frame=reset_frame,
        )
        return RunResult(result)

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
        return self.controller.submit(
            image_node_id=image_node_id,
            output_node_id=output_node_id,
            output_port_name=output_port_name,
            image_port_name=image_port_name,
            image_array=image_array,
            image_base64=image_base64,
            image_path=image_path,
            max_steps=max_steps,
            reset_frame=reset_frame,
        )

    def get_result(self, task_id: str) -> TaskStatus:
        return TaskStatus(self.controller.get_result(task_id))

    def wait_for_result(
        self,
        task_id: str,
        *,
        poll_interval: float = 0.05,
        timeout: float = 120.0,
    ) -> RunResult:
        status = self.controller.wait_for_result(
            task_id, poll_interval=poll_interval, timeout=timeout)
        if status["status"] == "error":
            raise MultimodalError(f"Task {task_id} failed: {status.get('error')}")
        return RunResult(status["result"])

    def cancel_task(self, task_id: str) -> bool:
        return self.controller.cancel_task(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self.controller.list_tasks()


# ===========================================================================
# SharedMemoryClient — cross-process, zero-copy (via shared memory)
# ===========================================================================
class SharedMemoryClient:
    """Cross-process client using shared memory for zero-copy image transfer.

    Image data is transferred via ``multiprocessing.shared_memory`` — no
    base64 encoding, no pickling of pixel data.  Only small control messages
    (node IDs, array shapes, dtypes) go through a Unix domain socket.

    Usage::

        # Terminal 1: start the headless server
        python run_headless.py my_graph.json --server

        # Terminal 2: connect from a separate process
        from multimodal_client import SharedMemoryClient
        import cv2

        client = SharedMemoryClient("/tmp/mne_headless.sock")
        info = client.graph_info()
        img_node = info.find_node_by_name("Image")

        image = cv2.imread("photo.jpg")
        result = client.run(
            image_node_id=img_node.id,
            image_array=image,          # zero-copy via shared memory
            output_node_id=img_node.id,
            output_port_name="image_out",
        )
        # result.output is a raw numpy array
        cv2.imwrite("out.jpg", result.output)
    """

    def __init__(self, address: str = "/tmp/mne_headless.sock"):
        """Connect to a SharedMemoryServer.

        Args:
            address: socket path (Unix) or pipe name (Windows).
        """
        # Import the SharedMemoryClient from headless_api
        # We try to import it from the backend dir first; if that fails,
        # we fall back to a bundled implementation.
        try:
            # Try to find headless_api.py in common locations
            candidates = [
                Path(__file__).parent.parent / "mini-services" / "node-editor-server",
                Path(__file__).parent.parent / "mini-services" / "node-editor-server",
                Path(__file__).parent,
            ]
            for c in candidates:
                if (c / "headless_api.py").exists():
                    if str(c) not in sys.path:
                        sys.path.insert(0, str(c))
                    break
            from headless_api import SharedMemoryClient as _ShmClient
            self._client = _ShmClient(address)
        except ImportError:
            raise MultimodalError(
                "Could not import headless_api.py. Make sure the backend directory "
                "(mini-services/node-editor-server) is accessible. "
                "Alternatively, use DirectClient for in-process usage or "
                "HttpClient for HTTP-based communication."
            )

    def graph_info(self) -> GraphInfo:
        return GraphInfo(self._client.graph_info())

    def find_node_by_name(self, name: str) -> NodeInfo:
        info = self.graph_info()
        node = info.find_node_by_name(name)
        if node is None:
            raise MultimodalError(f"Node '{name}' not found")
        return node

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
    ) -> RunResult:
        """Push an image and run one frame synchronously.

        ``image_array`` (numpy array) is transferred via shared memory —
        zero-copy, no base64 encoding.
        """
        result = self._client.run(
            image_node_id=image_node_id,
            output_node_id=output_node_id,
            output_port_name=output_port_name,
            image_port_name=image_port_name,
            image_array=image_array,
            image_base64=image_base64,
            image_path=image_path,
            max_steps=max_steps,
            reset_frame=reset_frame,
        )
        return RunResult(result)

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
        return self._client.submit(
            image_node_id=image_node_id,
            output_node_id=output_node_id,
            output_port_name=output_port_name,
            image_port_name=image_port_name,
            image_array=image_array,
            image_base64=image_base64,
            image_path=image_path,
            max_steps=max_steps,
            reset_frame=reset_frame,
        )

    def get_result(self, task_id: str) -> TaskStatus:
        return TaskStatus(self._client.get_result(task_id))

    def wait_for_result(
        self,
        task_id: str,
        *,
        poll_interval: float = 0.05,
        timeout: float = 120.0,
    ) -> RunResult:
        status = self._client.wait_for_result(
            task_id, poll_interval=poll_interval, timeout=timeout)
        if status["status"] == "error":
            raise MultimodalError(f"Task {task_id} failed: {status.get('error')}")
        return RunResult(status["result"])

    def cancel_task(self, task_id: str) -> bool:
        return self._client.cancel_task(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self._client.list_tasks()

    def ping(self) -> bool:
        return self._client.ping()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ===========================================================================
# HttpClient — HTTP-based client for the GUI backend (cross-machine)
# ===========================================================================
class HttpClient:
    """HTTP client for the GUI backend.

    Image data is base64-encoded — use this only for cross-machine scenarios
    or when the GUI backend is already running.  For same-machine usage,
    prefer DirectClient (in-process) or SharedMemoryClient (cross-process).
    """

    def __init__(self, base_url: str = "http://localhost:3030", timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
                if not payload:
                    return {}
                return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                detail = err_body.get("detail", err_body)
            except Exception:
                detail = e.read().decode("utf-8", errors="replace")
            raise MultimodalError(f"HTTP {e.code} {e.reason}: {detail}") from None
        except urllib.error.URLError as e:
            raise MultimodalError(f"Connection error: {e.reason}") from None

    @staticmethod
    def _encode_image(image_path, image_bytes, image_base64, image_mime="image/jpeg"):
        if image_path is not None:
            p = Path(image_path)
            if not p.exists():
                raise MultimodalError(f"Image file not found: {p}")
            raw = p.read_bytes()
            suffix = p.suffix.lower().lstrip(".")
            if suffix in ("jpg", "jpeg"):
                image_mime = "image/jpeg"
            elif suffix:
                image_mime = f"image/{suffix}"
            return f"data:{image_mime};base64,{base64.b64encode(raw).decode('ascii')}"
        if image_bytes is not None:
            return f"data:{image_mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        if image_base64 is not None:
            if image_base64.startswith("data:"):
                return image_base64
            return f"data:{image_mime};base64,{image_base64}"
        raise MultimodalError("No image supplied")

    def graph_info(self) -> GraphInfo:
        return GraphInfo(self._request("GET", "/api/external/graph-info"))

    def find_node_by_name(self, name: str) -> NodeInfo:
        info = self.graph_info()
        node = info.find_node_by_name(name)
        if node is None:
            raise MultimodalError(f"Node '{name}' not found")
        return node

    def ping(self) -> Dict[str, Any]:
        """Health check — returns ``{"ok": true, "mode": "gui-http"}``.

        The ``mode`` field distinguishes the backend type:
          - ``"gui-http"``     : GUI backend (FastAPI, browser UI running)
          - ``"headless-shm"`` : headless backend (shared-memory transport)

        Useful for auto-detecting which backend you're connected to.
        """
        try:
            return self._request("GET", "/api/external/ping")
        except MultimodalError:
            return {"ok": False, "mode": "unknown"}

    def run(
        self,
        *,
        image_node_id: str,
        output_node_id: str,
        output_port_name: str,
        image_port_name: str = "image_out",
        image_array: Optional[Any] = None,
        image_bytes: Optional[bytes] = None,
        image_base64: Optional[str] = None,
        image_path: Optional[str] = None,
        max_steps: int = 50,
        reset_frame: bool = True,
    ) -> RunResult:
        """Push an image and run one frame synchronously (HTTP, base64-encoded)."""
        # Convert numpy array to bytes if needed
        if image_array is not None:
            try:
                import cv2
                import numpy as np
                if isinstance(image_array, np.ndarray):
                    ok, buf = cv2.imencode(".jpg", image_array, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    if ok:
                        image_bytes = buf.tobytes()
            except ImportError:
                raise MultimodalError("opencv-python is required for image_array with HttpClient")
        b64 = self._encode_image(image_path, image_bytes, image_base64)
        body = {
            "image_node_id": image_node_id,
            "image_port_name": image_port_name,
            "image_base64": b64,
            "output_node_id": output_node_id,
            "output_port_name": output_port_name,
            "max_steps": max_steps,
            "reset_frame": reset_frame,
        }
        return RunResult(self._request("POST", "/api/external/run", body))

    def submit(self, **kwargs) -> str:
        # Convert numpy array to bytes if needed (same as run())
        image_array = kwargs.pop("image_array", None)
        if image_array is not None:
            try:
                import cv2
                import numpy as np
                if isinstance(image_array, np.ndarray):
                    ok, buf = cv2.imencode(".jpg", image_array, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    if ok:
                        kwargs["image_bytes"] = buf.tobytes()
            except ImportError:
                raise MultimodalError("opencv-python is required for image_array with HttpClient")
        b64 = self._encode_image(
            kwargs.pop("image_path", None),
            kwargs.pop("image_bytes", None),
            kwargs.pop("image_base64", None),
        )
        body = {**kwargs, "image_base64": b64}
        return self._request("POST", "/api/external/submit", body)["task_id"]

    def get_result(self, task_id: str) -> TaskStatus:
        return TaskStatus(self._request("GET", f"/api/external/result/{task_id}"))

    def wait_for_result(self, task_id: str, *, poll_interval=0.1, timeout=120.0) -> RunResult:
        start = time.time()
        while True:
            status = self.get_result(task_id)
            if status.is_done:
                if status.is_error:
                    raise MultimodalError(f"Task {task_id} failed: {status.error}")
                return status.result
            if time.time() - start > timeout:
                raise MultimodalError(f"Task {task_id} timed out")
            time.sleep(poll_interval)

    def cancel_task(self, task_id: str) -> bool:
        try:
            self._request("DELETE", f"/api/external/result/{task_id}")
            return True
        except MultimodalError:
            return False

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/external/tasks").get("tasks", [])


# Backward-compat alias
MultimodalClient = HttpClient


# ===========================================================================
# CLI
# ===========================================================================
def _cli_main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Push an image into the Multimodal Node Editor backend and read back an output.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ping — health check + backend mode detection
    p_ping = sub.add_parser("ping", help="Health check + detect backend mode (GUI vs headless)")
    p_ping.add_argument("--transport", default="http", choices=["shm", "http"],
                        help="Transport mode (default: http)")
    p_ping.add_argument("--address", default="/tmp/mne_headless.sock", help="Socket path (shm mode)")
    p_ping.add_argument("--base-url", default="http://localhost:3030", help="HTTP URL (http mode)")

    # info
    p_info = sub.add_parser("info", help="Show graph info (nodes & ports)")
    p_info.add_argument("--transport", default="http", choices=["direct", "shm", "http"],
                        help="Transport mode (default: http)")
    p_info.add_argument("--backend-dir", default=None, help="Backend dir (direct mode)")
    p_info.add_argument("--address", default="/tmp/mne_headless.sock", help="Socket path (shm mode)")
    p_info.add_argument("--base-url", default="http://localhost:3030", help="HTTP URL (http mode)")
    p_info.add_argument("--graph", default=None, help="Graph JSON path (direct mode)")

    # run (sync)
    p_run = sub.add_parser("run", help="Synchronous: push image, run, save output")
    p_run.add_argument("--transport", default="http", choices=["direct", "shm", "http"])
    p_run.add_argument("--backend-dir", default=None)
    p_run.add_argument("--address", default="/tmp/mne_headless.sock")
    p_run.add_argument("--base-url", default="http://localhost:3030")
    p_run.add_argument("--graph", default=None)
    p_run.add_argument("--image-node", required=True)
    p_run.add_argument("--image-port", default="image_out")
    p_run.add_argument("--image", required=True)
    p_run.add_argument("--output-node", required=True)
    p_run.add_argument("--output-port", required=True)
    p_run.add_argument("--save", default=None)

    args = parser.parse_args()

    # Create client based on transport
    if args.transport == "direct":
        if not args.backend_dir:
            print("--backend-dir is required for direct mode")
            sys.exit(1)
        client = DirectClient(args.backend_dir, graph_path=args.graph)
    elif args.transport == "shm":
        client = SharedMemoryClient(args.address)
    else:
        client = HttpClient(args.base_url)

    if args.cmd == "ping":
        if args.transport == "shm":
            ok = client.ping()
            mode = "headless-shm" if ok else "unreachable"
            print(f"Backend: {'reachable' if ok else 'unreachable'}")
            print(f"  mode: {mode}")
            print(f"  address: {args.address}")
        else:
            r = client.ping()
            print(f"Backend: {'reachable' if r.get('ok') else 'unreachable'}")
            print(f"  mode: {r.get('mode', 'unknown')}")
            print(f"  url: {args.base_url}")

    elif args.cmd == "info":
        # Show backend mode first (if available)
        if hasattr(client, "ping"):
            try:
                if args.transport == "shm":
                    ok = client.ping()
                    mode = "headless-shm" if ok else "unreachable"
                else:
                    r = client.ping()
                    mode = r.get("mode", "unknown")
                print(f"Backend mode: {mode}")
                print()
            except Exception:
                pass
        info = client.graph_info()
        print(info)
        for n in info.nodes:
            tag = " [source]" if n.is_source else ""
            print(f"  {n.id}  {n.name!r}  ({n.definition_id}){tag}")

    elif args.cmd == "run":
        import cv2
        image = cv2.imread(args.image)
        if image is None:
            print(f"Failed to read image: {args.image}")
            sys.exit(1)
        result = client.run(
            image_node_id=args.image_node,
            image_port_name=args.image_port,
            image_array=image,
            output_node_id=args.output_node,
            output_port_name=args.output_port,
        )
        print(result)
        if result.errors:
            print("Errors:")
            for nid, err in result.errors.items():
                print(f"  [{nid}] {err}")
        if args.save:
            result.save_output(args.save)
            print(f"Saved output to {args.save}")


if __name__ == "__main__":
    _cli_main()
