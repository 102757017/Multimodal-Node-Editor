# Python Client for Multimodal Node Editor

A Python client library that lets an external Python process push images into
a designated Image node on the backend's computation graph and read back the
output of any node.

## Works with BOTH backends

The Python client supports **both** the GUI backend and the headless backend:

| Backend | How to start | Best client transport |
|---------|-------------|----------------------|
| **GUI backend** (FastAPI, browser UI) | `python run_gui.py` | `HttpClient` (HTTP, base64) |
| **Headless backend** (no browser) | `python run_headless.py graph.json --server` | `SharedMemoryClient` (zero-copy) |
| **In-process** (import as library) | `from run_headless import HeadlessController` | `DirectClient` (zero-overhead) |

The **same Python client API** works against all three — just pick the
transport that matches your backend.

## Three transport modes

| Transport | Class | Image transfer | Use case |
|-----------|-------|----------------|----------|
| **Direct** | `DirectClient` | In-process (numpy by reference) | External script imports backend as a library — **zero overhead** |
| **Shared memory** | `SharedMemoryClient` | Shared memory (zero-copy) | Cross-process, headless backend — **no base64, no serialization** |
| **HTTP** | `HttpClient` | Base64-encoded | **GUI backend** (while browser is open) or cross-machine |

### Which transport should I use?

- **GUI backend is running** (you want to see the graph in the browser while
  a script feeds it images): use `HttpClient` → `http://localhost:3030`
- **Headless backend** (production, no browser): use `SharedMemoryClient` →
  `/tmp/mne_headless.sock` (zero-copy, most efficient)
- **Same process** (plugin, Jupyter, script): use `DirectClient` (zero-overhead)
- **Cross-machine**: use `HttpClient` (only option that works across machines)

## Installation

The sync client uses only the Python standard library. For the async client,
install `aiohttp`:

```bash
pip install aiohttp  # only for AsyncMultimodalClient
```

For image handling, install `opencv-python` and `numpy`:

```bash
pip install opencv-python numpy
```

## Quick start — DirectClient (in-process, most efficient)

```python
import sys
sys.path.insert(0, "/path/to/mini-services/node-editor-server")

from multimodal_client import DirectClient
import cv2

# Create an in-process client — loads the graph directly
client = DirectClient(
    backend_dir="/path/to/mini-services/node-editor-server",
    graph_path="my_graph.json",
)

# Discover the graph topology
info = client.graph_info()
img_node = info.find_node_by_name("Image")

# Push a raw numpy array — zero-copy, no base64!
image = cv2.imread("photo.jpg")
result = client.run(
    image_node_id=img_node.id,
    image_array=image,          # <-- raw numpy, passed by reference
    output_node_id=img_node.id,
    output_port_name="image_out",
)

# result.output is a raw numpy array — no base64 decoding needed
cv2.imwrite("output.jpg", result.output)
```

## Quick start — SharedMemoryClient (cross-process, zero-copy)

```bash
# Terminal 1: start the headless server
python run_headless.py my_graph.json --server
# Output: Shared-memory server listening on: /tmp/mne_headless.sock
```

```python
# Terminal 2: connect from a separate process
from multimodal_client import SharedMemoryClient
import cv2

client = SharedMemoryClient("/tmp/mne_headless.sock")

info = client.graph_info()
img_node = info.find_node_by_name("Image")

# Push a raw numpy array — transferred via shared memory (zero-copy)
image = cv2.imread("photo.jpg")
result = client.run(
    image_node_id=img_node.id,
    image_array=image,          # <-- zero-copy via shared memory
    output_node_id=img_node.id,
    output_port_name="image_out",
)

# result.output is a raw numpy array
cv2.imwrite("output.jpg", result.output)
```

## Quick start — HttpClient (GUI backend, HTTP)

Use this when the GUI backend is running (`python run_gui.py`) and you want
to feed it images from a script while watching the graph in the browser.

```bash
# Terminal 1: start the GUI backend
python run_gui.py
# → FastAPI on http://localhost:3030, browser opens at http://localhost:3000
```

```python
# Terminal 2: feed images from a script
from multimodal_client import HttpClient
import cv2

client = HttpClient("http://localhost:3030")

# Check which backend we're connected to
print(client.ping())  # {'ok': True, 'mode': 'gui-http'}

info = client.graph_info()
img_node = info.find_node_by_name("Image")

# Push an image — base64-encoded over HTTP
image = cv2.imread("photo.jpg")
result = client.run(
    image_node_id=img_node.id,
    image_array=image,          # <-- encoded to base64 internally
    output_node_id=img_node.id,
    output_port_name="image_out",
)

# result.output is a base64 data URI (decode with result.decode_image())
output = result.decode_image()  # numpy array
cv2.imwrite("output.jpg", output)
```

You can also build/load the graph via the GUI (browser) and then drive it
from the script — the script and the browser share the same backend graph.

## Async usage

All three clients support async submission:

```python
from multimodal_client import SharedMemoryClient

client = SharedMemoryClient("/tmp/mne_headless.sock")

# Submit (returns immediately with a task id)
task_id = client.submit(
    image_node_id="node-abc12345",
    image_array=cv2.imread("image1.jpg"),
    output_node_id="node-def67890",
    output_port_name="result",
)

# ... do other work ...

# Wait for the result (blocks until done or timeout)
result = client.wait_for_result(task_id, timeout=60.0)
print(result)
```

## CLI

```bash
# Health check + detect backend mode (GUI vs headless)
python -m multimodal_client ping --transport http --base-url http://localhost:3030
# → Backend: reachable
#   mode: gui-http
#   url: http://localhost:3030

python -m multimodal_client ping --transport shm --address /tmp/mne_headless.sock
# → Backend: reachable
#   mode: headless-shm
#   address: /tmp/mne_headless.sock

# Show graph info (GUI backend, HTTP)
python -m multimodal_client info --transport http --base-url http://localhost:3030

# Synchronous run (GUI backend, HTTP)
python -m multimodal_client run \
    --transport http \
    --base-url http://localhost:3030 \
    --image-node node-abc12345 \
    --image /path/to/image.jpg \
    --output-node node-def67890 \
    --output-port result \
    --save output.jpg

# Synchronous run (headless backend, shared memory — most efficient)
python -m multimodal_client run \
    --transport shm \
    --address /tmp/mne_headless.sock \
    --image-node node-abc12345 \
    --image /path/to/image.jpg \
    --output-node node-def67890 \
    --output-port result \
    --save output.jpg

# In-process mode (zero-overhead)
python -m multimodal_client run \
    --transport direct \
    --backend-dir /path/to/mini-services/node-editor-server \
    --graph my_graph.json \
    --image-node node-abc12345 \
    --image /path/to/image.jpg \
    --output-node node-def67890 \
    --output-port result \
    --save output.jpg
```

## API reference

All three clients share the same API:

- `graph_info() -> GraphInfo` — list nodes & ports
- `find_node_by_name(name) -> NodeInfo` — convenience lookup
- `ping() -> dict` — health check; returns `{"ok": true, "mode": "gui-http"}` or `{"mode": "headless-shm"}`
- `run(*, image_node_id, output_node_id, output_port_name, image_array=..., image_path=..., ...) -> RunResult` — synchronous run
- `submit(...) -> str` — async submit, returns task id
- `get_result(task_id) -> TaskStatus` — single poll
- `wait_for_result(task_id, *, timeout=120.0) -> RunResult` — block until done
- `cancel_task(task_id) -> bool`
- `list_tasks() -> list[dict]`

### RunResult

- `.status` — `"frame_complete"`, `"idle"`, or `"exhausted"`
- `.output` — the output value (raw numpy array for DirectClient/SharedMemoryClient, base64 data URI for HttpClient)
- `.is_image` — True if output is an image
- `.save_output(path)` — save to disk
- `.decode_image()` — decode to numpy array (works for all transports)
- `.errors` — `{node_id: error_message}`
- `.elapsed_ms` — execution time

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  External Python Process                                     │
│                                                               │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────┐ │
│  │  DirectClient   │  │ SharedMemoryClient│  │  HttpClient │ │
│  │  (in-process)   │  │ (cross-process)  │  │ (cross-mach)│ │
│  └────────┬────────┘  └────────┬─────────┘  └──────┬──────┘ │
│           │                    │                   │        │
│           │ numpy by ref       │ shared memory     │ HTTP   │
│           │ (zero-overhead)    │ (zero-copy)       │ (base64)│
└───────────┼────────────────────┼───────────────────┼────────┘
            │                    │                   │
┌───────────▼────────────────────▼───────────────────▼────────┐
│  Backend Process                                             │
│                                                               │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────┐ │
│  │ HeadlessControl │  │ SharedMemoryServer│  │  FastAPI    │ │
│  │    (direct)     │  │   (--server)     │  │  (main.py)  │ │
│  └─────────────────┘  └──────────────────┘  └─────────────┘ │
│           │                    │                   │        │
│           └────────────┬───────┴───────────────────┘        │
│                        ▼                                     │
│                 ┌──────────────┐                             │
│                 │  Graph Engine│  (core.py)                  │
│                 └──────────────┘                             │
└──────────────────────────────────────────────────────────────┘
```
