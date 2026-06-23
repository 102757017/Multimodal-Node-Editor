"""End-to-end test for the multimodal_client.

Spins up a tiny mock HTTP server implementing the /api/external/* endpoints,
runs both sync and async methods against it, and verifies the results.

Run with:  python test_client.py
"""
from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# add this directory to path so we can import the client
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from multimodal_client import (
    MultimodalClient,
    RunResult,
    TaskStatus,
    MultimodalError,
)


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------
MOCK_GRAPH = {
    "graph_id": "test-graph",
    "node_count": 2,
    "connection_count": 1,
    "nodes": [
        {
            "id": "node-img1",
            "name": "Image",
            "definition_id": "image.input.image",
            "is_source": True,
            "inputs": [],
            "outputs": [{"id": "port-img-out", "name": "image_out",
                         "display_name": "Image Out", "data_type": "image"}],
        },
        {
            "id": "node-disp1",
            "name": "Display",
            "definition_id": "image.output.display",
            "is_source": False,
            "inputs": [{"id": "port-disp-in", "name": "image",
                        "display_name": "Image", "data_type": "image"}],
            "outputs": [{"id": "port-disp-out", "name": "image_out",
                         "display_name": "Image Out", "data_type": "image"}],
        },
    ],
}

MOCK_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ"
    "/pLvAAAAAElFTkSuQmCC"
)

# In-memory async task store for the mock server
_tasks = {}
_tasks_lock = threading.Lock()


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        if self.path == "/api/external/graph-info":
            self._send(200, MOCK_GRAPH)
        elif self.path == "/api/external/tasks":
            with _tasks_lock:
                items = [
                    {"task_id": tid, "status": t["status"],
                     "created_at": t.get("created_at"),
                     "started_at": t.get("started_at"),
                     "completed_at": t.get("completed_at")}
                    for tid, t in _tasks.items()
                ]
            self._send(200, {"tasks": items})
        elif self.path.startswith("/api/external/result/"):
            tid = self.path.split("/")[-1]
            with _tasks_lock:
                t = _tasks.get(tid)
            if t is None:
                self._send(404, {"detail": "task not found"})
            else:
                self._send(200, {
                    "task_id": tid,
                    "status": t["status"],
                    "created_at": t.get("created_at"),
                    "started_at": t.get("started_at"),
                    "completed_at": t.get("completed_at"),
                    "result": t.get("result"),
                    "error": t.get("error"),
                })
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self):
        if self.path == "/api/external/run":
            body = self._read_body()
            # simulate a frame execution result
            self._send(200, {
                "status": "frame_complete",
                "frame_id": 1,
                "executed_nodes": [body["image_node_id"], body["output_node_id"]],
                "skipped_nodes": [],
                "waiting_nodes": [],
                "errors": {},
                "elapsed_ms": 12.3,
                "output": f"data:image/png;base64,{MOCK_IMAGE_B64}",
                "output_port_data_type": "image",
            })
        elif self.path == "/api/external/submit":
            import uuid
            tid = f"task-{uuid.uuid4().hex[:12]}"
            with _tasks_lock:
                _tasks[tid] = {
                    "status": "pending",
                    "created_at": time.time(),
                    "started_at": None,
                    "completed_at": None,
                    "result": None,
                    "error": None,
                }

            # background worker that flips the task to completed after 0.2s
            def worker():
                time.sleep(0.2)
                with _tasks_lock:
                    _tasks[tid]["status"] = "running"
                    _tasks[tid]["started_at"] = time.time()
                time.sleep(0.1)
                with _tasks_lock:
                    _tasks[tid]["status"] = "completed"
                    _tasks[tid]["completed_at"] = time.time()
                    _tasks[tid]["result"] = {
                        "status": "frame_complete",
                        "frame_id": 1,
                        "executed_nodes": ["node-img1", "node-disp1"],
                        "skipped_nodes": [],
                        "waiting_nodes": [],
                        "errors": {},
                        "elapsed_ms": 8.4,
                        "output": f"data:image/png;base64,{MOCK_IMAGE_B64}",
                        "output_port_data_type": "image",
                    }

            threading.Thread(target=worker, daemon=True).start()
            self._send(200, {"task_id": tid, "status": "pending"})
        else:
            self._send(404, {"detail": "not found"})

    def do_DELETE(self):
        if self.path.startswith("/api/external/result/"):
            tid = self.path.split("/")[-1]
            with _tasks_lock:
                if tid in _tasks:
                    del _tasks[tid]
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"detail": "task not found"})
        else:
            self._send(404, {"detail": "not found"})


def run_mock_server(port: int):
    httpd = HTTPServer(("127.0.0.1", port), MockHandler)
    httpd.serve_forever()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def main():
    port = 18923
    server_thread = threading.Thread(target=run_mock_server, args=(port,), daemon=True)
    server_thread.start()
    time.sleep(0.3)  # let the server come up

    base_url = f"http://127.0.0.1:{port}"
    client = MultimodalClient(base_url, timeout=10.0)

    print("=" * 60)
    print("Test 1: graph_info()")
    info = client.graph_info()
    assert info.node_count == 2, f"expected 2 nodes, got {info.node_count}"
    img = info.find_node_by_name("Image")
    assert img is not None and img.id == "node-img1"
    disp = info.find_node_by_name("Display")
    assert disp is not None and disp.id == "node-disp1"
    print(f"  ✓ graph has {info.node_count} nodes, Image={img.id}, Display={disp.id}")

    print("=" * 60)
    print("Test 2: sync run()")
    result = client.run(
        image_node_id=img.id,
        image_path=None,
        image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
        output_node_id=disp.id,
        output_port_name="image_out",
    )
    assert result.status == "frame_complete"
    assert result.is_image
    print(f"  ✓ status={result.status}, frame_id={result.frame_id}, "
          f"elapsed={result.elapsed_ms:.1f}ms")
    print(f"  ✓ output is image: {result.is_image}, type={result.output_port_data_type}")

    print("=" * 60)
    print("Test 3: async submit() + wait_for_result()")
    task_id = client.submit(
        image_node_id=img.id,
        image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
        output_node_id=disp.id,
        output_port_name="image_out",
    )
    print(f"  submitted task: {task_id}")
    result2 = client.wait_for_result(task_id, poll_interval=0.05, timeout=5.0)
    assert result2.status == "frame_complete"
    assert result2.is_image
    print(f"  ✓ task completed, status={result2.status}, "
          f"elapsed={result2.elapsed_ms:.1f}ms")

    print("=" * 60)
    print("Test 4: list_tasks()")
    tasks = client.list_tasks()
    assert len(tasks) >= 1
    print(f"  ✓ {len(tasks)} task(s) listed")

    print("=" * 60)
    print("Test 5: save_output()")
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "out.png")
        result.save_output(path)
        size = os.path.getsize(path)
        assert size > 0
        print(f"  ✓ saved {size} bytes to {path}")

    print("=" * 60)
    print("Test 6: error handling (unknown task id)")
    try:
        client.get_result("task-nonexistent")
        print("  ✗ should have raised")
    except MultimodalError as e:
        print(f"  ✓ got expected error: {e}")

    print("=" * 60)
    print("All tests passed!")


if __name__ == "__main__":
    main()
