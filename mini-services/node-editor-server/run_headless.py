"""
run_headless.py - Headless execution runner for the refactored node editor.

Runs a saved graph.json without any web UI or FastAPI server, giving maximum
performance:
  * No base64 encoding overhead (numpy arrays stay numpy internally).
  * No HTTP round-trips — direct in-process execution.
  * Uses the refactored three-state engine (frame_complete / idle / exhausted)
    with frame sync, ANY/ALL trigger modes, and the global ModelRegistry so
    multiple nodes sharing the same large model (CLIP/LLM/ONNX) load ONE copy.
  * Supports both batch mode (fixed input) and streaming mode (source nodes
    push frames until depleted).

Usage:
    python run_headless.py <graph.json>
    python run_headless.py graph.json --count 10
    python run_headless.py graph.json --interval 100
    python run_headless.py graph.json --no-display        # no cv2 windows
    python run_headless.py graph.json --output-dir out/    # save images
    python run_headless.py graph.json --stream             # streaming mode

The graph.json format matches what the FastAPI /api/graph/save endpoint
produces (and what /api/graph returns).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Make the mini-service importable when run from anywhere
SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER_DIR))

# Import the refactored engine + discovery (auto-registers all 131 nodes)
import node_def  # noqa: F401  (triggers discovery)
from core import Graph
from models import Connection, Node
from node_def import get_node_definition, list_node_definitions


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------
def load_graph(path: Path) -> Graph:
    """Load a graph.json into a Graph instance (refactored engine)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    g = Graph(sync_timeout=float(data.get("sync_timeout", 5.0)))
    g.id = data.get("id", g.id)
    g.graph_format_version = data.get("graph_format_version", "1.0.0")
    for n_data in data.get("nodes", []):
        # rebuild Node with dynamic_port_configs
        node = Node(**n_data)
        g.add_node(node)
    for c_data in data.get("connections", []):
        conn = Connection(**c_data)
        g.connections.append(conn)
    g._mark_dirty()
    return g


def find_source_nodes(g: Graph) -> List[Node]:
    """Return all source nodes (no input ports, or is_source_node=True)."""
    return [n for n in g.nodes if n.effective_is_source]


def find_terminal_outputs(g: Graph) -> Set[str]:
    """Return the set of 'node_id.port_name' keys for output ports that have
    no downstream connection (terminal outputs — these are what we display/save)."""
    used_output_port_ids = {c.from_port_id for c in g.connections}
    terminal = set()
    for node in g.nodes:
        for op in node.outputs:
            if op.id not in used_output_port_ids:
                terminal.add(f"{node.id}.{op.name}")
    return terminal


# ---------------------------------------------------------------------------
# Display / output
# ---------------------------------------------------------------------------
def display_result(
    result,
    g: Graph,
    terminal_keys: Set[str],
    execution_count: int,
    output_dir: Optional[Path] = None,
    no_display: bool = False,
):
    """Print timing + errors, optionally show/save terminal images."""
    print(f"\n[Frame {result.frame_id} #{execution_count}] "
          f"status={result.status} {result.elapsed_ms:.1f}ms "
          f"exec={len(result.executed_nodes)} skip={len(result.skipped_nodes)} "
          f"wait={len(result.waiting_nodes)}")

    # node times (top 5)
    times = sorted(result.node_times.values(), key=lambda t: t["order"])[:5]
    for t in times:
        print(f"  {t['name']}: {t['time']:.1f}ms")

    if result.errors:
        print("  Errors:")
        for nid, err in list(result.errors.items())[:3]:
            name = next((n.name for n in g.nodes if n.id == nid), nid[:12])
            print(f"    [{name}] {err[:80]}")

    # terminal outputs
    for key in terminal_keys:
        if key not in result.outputs:
            continue
        val = result.outputs[key]
        node_id, port_name = key.rsplit(".", 1)
        node = next((n for n in g.nodes if n.id == node_id), None)
        label = f"{node.name if node else node_id[:12]}.{port_name}"

        # text / numeric
        if isinstance(val, (int, float, str)) and not isinstance(val, str) and len(str(val)) < 200:
            print(f"  {label} = {val}")
        elif isinstance(val, str) and len(val) < 200 and not val.startswith("data:"):
            print(f"  {label} = {val}")

        # image — save to file and/or show
        if isinstance(val, str) and val.startswith("data:image"):
            if output_dir:
                _save_image(val, output_dir, f"{label}_{execution_count:04d}.png")
            if not no_display:
                _show_image(val, label)


def _save_image(data_uri: str, out_dir: Path, filename: str):
    """Save a base64 data URI image to a file."""
    import base64
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
    with open(out_dir / filename, "wb") as f:
        f.write(base64.b64decode(raw))
    print(f"  saved: {out_dir / filename}")


def _show_image(data_uri: str, label: str):
    """Display an image in an OpenCV window."""
    try:
        import cv2
        import numpy as np
        import base64
        from PIL import Image
        import io
        raw = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
        arr = np.array(Image.open(io.BytesIO(base64.b64decode(raw))).convert("BGR"))
        cv2.imshow(label, arr)
        cv2.waitKey(1)
    except Exception as e:
        print(f"  (display failed: {e})")


# ---------------------------------------------------------------------------
# Batch execution mode
# ---------------------------------------------------------------------------
def run_batch(
    g: Graph,
    count: int,
    interval_ms: int,
    terminal_keys: Set[str],
    output_dir: Optional[Path],
    no_display: bool,
):
    """Run the graph `count` times (0 = infinite).  Each iteration is one frame.

    Source nodes use their default property values; for streaming sources
    (file_player, webcam, …) the node's own compute() advances through frames.
    """
    sources = find_source_nodes(g)
    print(f"\nSources: {len(sources)} ({', '.join(s.name for s in sources)})")
    print(f"Terminal outputs: {len(terminal_keys)}")
    print(f"Interval: {interval_ms}ms, Count: {'∞' if count == 0 else count}")
    print("=" * 60)

    execution_count = 0
    interval_sec = interval_ms / 1000.0
    try:
        while True:
            execution_count += 1
            start = time.time()

            # start a new frame
            g.start_frame()

            # run to frame_complete (or idle/exhausted)
            result = None
            for _ in range(20):
                result = g.execute_step()
                if result.status in ("frame_complete", "idle", "exhausted"):
                    break

            if result:
                display_result(result, g, terminal_keys, execution_count,
                               output_dir, no_display)

            # check exit conditions
            if result and result.status == "exhausted":
                print("\nAll sources depleted — stopping.")
                break
            if count > 0 and execution_count >= count:
                print(f"\nReached count limit ({count}).")
                break

            # interval wait
            elapsed = time.time() - start
            sleep_time = max(0, interval_sec - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user (Ctrl+C)")


# ---------------------------------------------------------------------------
# Streaming execution mode
# ---------------------------------------------------------------------------
def run_streaming(
    g: Graph,
    interval_ms: int,
    terminal_keys: Set[str],
    output_dir: Optional[Path],
    no_display: bool,
    max_frames: int = 10000,
):
    """Stream-mode: external loop calls execute_step(); idle→wait, exhausted→stop.

    In this mode source nodes are expected to push data via set_source_data()
    (simulated here by calling each source's compute() to produce the next frame).
    """
    sources = find_source_nodes(g)
    print(f"\nStreaming mode")
    print(f"Sources: {len(sources)}")
    print(f"Terminal outputs: {len(terminal_keys)}")
    print("=" * 60)

    execution_count = 0
    interval_sec = interval_ms / 1000.0
    try:
        while execution_count < max_frames:
            execution_count += 1
            start = time.time()

            # start a new frame
            g.start_frame()

            # let each source produce its next frame of data
            for src in sources:
                try:
                    definition = get_node_definition(src.definition_id, src.definition_version)
                    outputs = definition.compute({}, dict(src.properties), {"node_id": src.id})
                    # push the outputs onto the source's output ports
                    g.set_source_data(src.id, outputs)
                except Exception as e:
                    print(f"  source {src.name} error: {e}")
                    g.mark_source_depleted(src.id)

            # drain the frame
            result = None
            for _ in range(20):
                result = g.execute_step()
                if result.status in ("frame_complete", "idle", "exhausted"):
                    break

            if result:
                display_result(result, g, terminal_keys, execution_count,
                               output_dir, no_display)

            if result and result.status == "exhausted":
                print("\nAll sources depleted — stream ended.")
                break

            elapsed = time.time() - start
            sleep_time = max(0, interval_sec - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user (Ctrl+C)")


# ---------------------------------------------------------------------------
# External API: in-process + shared-memory transport
# ---------------------------------------------------------------------------
# The HeadlessController (in-process, zero-serialisation) and SharedMemoryServer
# (cross-process, zero-copy image transfer via multiprocessing.shared_memory)
# are implemented in headless_api.py.  We import them here so that
# `from run_headless import HeadlessController` works.
# ---------------------------------------------------------------------------
from headless_api import HeadlessController, SharedMemoryServer, SharedMemoryClient  # noqa: F401


def run_server(g: Graph, address: str = "/tmp/mne_headless.sock"):
    """Start the shared-memory server on top of the loaded headless graph.

    Image data is transferred via ``multiprocessing.shared_memory`` (zero-copy).
    Only small control messages (node IDs, array shapes, dtypes) go through
    a Unix domain socket / named pipe — no HTTP, no FastAPI, no base64
    encoding of pixel data.

    Args:
        g: the loaded Graph.
        address: socket path (Unix) or pipe name (Windows).
                 Default: /tmp/mne_headless.sock
    """
    ctrl = HeadlessController(g)
    server = SharedMemoryServer(ctrl, address=address)
    server.start()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Run a graph.json headlessly with the refactored execution engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_headless.py graph.json                  # run forever, show images
  python run_headless.py graph.json --count 10       # run 10 frames
  python run_headless.py graph.json --no-display     # no cv2 windows
  python run_headless.py graph.json --output-dir out # save terminal images
  python run_headless.py graph.json --stream         # streaming source mode
  python run_headless.py graph.json --list-nodes     # list available nodes
        """,
    )
    parser.add_argument("graph_file", nargs="?", help="Path to graph.json")
    parser.add_argument("--count", type=int, default=0,
                        help="Number of frames to run (0=infinite, default 0)")
    parser.add_argument("--interval", type=int, default=50,
                        help="Execution interval in ms (default 50)")
    parser.add_argument("--no-display", action="store_true",
                        help="Don't open cv2 windows")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory to save terminal output images")
    parser.add_argument("--stream", action="store_true",
                        help="Streaming mode: source nodes push frames each iteration")
    parser.add_argument("--list-nodes", action="store_true",
                        help="List all available node definitions and exit")
    parser.add_argument("--sync-timeout", type=float, default=5.0,
                        help="Frame-sync timeout in seconds (default 5.0)")
    parser.add_argument("--show-models", action="store_true",
                        help="Print the global model registry after each frame")
    parser.add_argument("--server", action="store_true",
                        help="Server mode: load the graph then start a shared-memory "
                             "server. Lets an external Python client push images (via "
                             "shared memory, zero-copy) and read outputs without running "
                             "the browser UI or any HTTP/FastAPI server.")
    parser.add_argument("--address", type=str, default="/tmp/mne_headless.sock",
                        help="Socket path (Unix) or pipe name (Windows) for the "
                             "shared-memory server (default: /tmp/mne_headless.sock)")
    args = parser.parse_args()

    if args.list_nodes:
        print(f"Available node definitions ({len(list_node_definitions())}):")
        for d in list_node_definitions():
            tag = " [source]" if d.is_source_node else ""
            dyn = " [dyn]" if d.dynamic_port_configs else ""
            print(f"  {d.definition_id:40s} {d.display_name:25s}{tag}{dyn}")
        return

    if not args.graph_file:
        parser.error("graph_file is required (or use --list-nodes)")

    graph_path = Path(args.graph_file)
    if not graph_path.exists():
        print(f"Error: graph file not found: {graph_path}")
        sys.exit(1)

    print(f"Loading graph: {graph_path}")
    g = load_graph(graph_path)
    g.sync_timeout = args.sync_timeout
    g.exec_state.sync_timeout = args.sync_timeout

    print(f"  Nodes: {len(g.nodes)}")
    print(f"  Connections: {len(g.connections)}")
    print(f"  Sync timeout: {args.sync_timeout}s")

    terminal_keys = find_terminal_outputs(g)
    output_dir = Path(args.output_dir) if args.output_dir else None

    if args.server:
        # ----------------------------------------------------------------- #
        # Server mode: start the shared-memory server.  No HTTP, no FastAPI,
        # no base64 encoding of images.  Image data is transferred via
        # multiprocessing.shared_memory (zero-copy).  Only small control
        # messages go through a Unix domain socket / named pipe.
        # ----------------------------------------------------------------- #
        run_server(g, address=args.address)
        return

    if args.stream:
        run_streaming(g, args.interval, terminal_keys, output_dir, args.no_display)
    else:
        run_batch(g, args.count, args.interval, terminal_keys, output_dir, args.no_display)

    # final cleanup
    if not args.no_display:
        try:
            import cv2
            cv2.destroyAllWindows()
        except Exception:
            pass

    # show model registry if requested
    if args.show_models:
        from model_registry import registry
        snap = registry.snapshot()
        print(f"\nModel Registry: {snap['entry_count']} entries, "
              f"{snap['total_mb']:.1f} MB")
        for e in snap["entries"]:
            print(f"  {e['key']} ({e['label']}, {e['est_mb']}MB, "
                  f"loads={e['load_count']} hits={e['hit_count']})")

    print("\nDone.")


if __name__ == "__main__":
    main()
