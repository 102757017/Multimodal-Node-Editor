"""End-to-end test for the async (aiohttp) client.

Reuses the same mock backend as test_client.py.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from multimodal_client import AsyncMultimodalClient, MultimodalError

# reuse the mock from test_client
from test_client import MockHandler, MOCK_GRAPH, MOCK_IMAGE_B64  # noqa


def run_mock_server(port: int):
    httpd = HTTPServer(("127.0.0.1", port), MockHandler)
    httpd.serve_forever()


async def main():
    port = 18924
    server_thread = threading.Thread(target=run_mock_server, args=(port,), daemon=True)
    server_thread.start()
    await asyncio.sleep(0.3)

    base_url = f"http://127.0.0.1:{port}"

    print("=" * 60)
    print("Async Test 1: graph_info + run()")
    async with AsyncMultimodalClient(base_url, timeout=10.0) as client:
        info = await client.graph_info()
        assert info.node_count == 2
        img = info.find_node_by_name("Image")
        disp = info.find_node_by_name("Display")
        print(f"  ✓ graph_info: {info.node_count} nodes")

        result = await client.run(
            image_node_id=img.id,
            image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
            output_node_id=disp.id,
            output_port_name="image_out",
        )
        assert result.status == "frame_complete"
        print(f"  ✓ sync run: status={result.status}, elapsed={result.elapsed_ms:.1f}ms")

        print("=" * 60)
        print("Async Test 2: submit + wait_for_result")
        task_ids = await asyncio.gather(*[
            client.submit(
                image_node_id=img.id,
                image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
                output_node_id=disp.id,
                output_port_name="image_out",
            )
            for _ in range(3)
        ])
        print(f"  submitted {len(task_ids)} tasks: {task_ids}")
        results = await asyncio.gather(*[
            client.wait_for_result(tid, poll_interval=0.05, timeout=5.0)
            for tid in task_ids
        ])
        for r in results:
            assert r.status == "frame_complete"
        print(f"  ✓ all {len(results)} tasks completed")

        print("=" * 60)
        print("Async Test 3: run_async (submit+poll wrapper)")
        r = await client.run_async(
            image_node_id=img.id,
            image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
            output_node_id=disp.id,
            output_port_name="image_out",
        )
        assert r.status == "frame_complete"
        print(f"  ✓ run_async: status={r.status}")

        print("=" * 60)
        print("Async Test 4: error handling")
        try:
            await client.get_result("task-nonexistent")
            print("  ✗ should have raised")
        except MultimodalError as e:
            print(f"  ✓ got expected error: {e}")

    print("=" * 60)
    print("All async tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
