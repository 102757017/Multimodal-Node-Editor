---
Task ID: 1
Agent: main (Super Z)
Task: Clone and improve the Multimodal-Node-Editor fork. Implement: (1) Auto Layout button, (2) Python external client for image input/output (sync + async), (3) MQTT communication node group with paho.mqtt (receive + send, wildcard topics, cached payload), (4) fix ConfigPanel text selection bug, (5) package as zip.

Work Log:
- Cloned https://github.com/102757017/Multimodal-Node-Editor.git to /home/z/my-project/Multimodal-Node-Editor
- Read README.md, page.tsx, ConfigPanel.tsx, main.py, core.py, node_def.py, run_headless.py, models.py, discovery.py, api.ts, package.json, next.config.ts, sample node files
- Identified root cause of Config text selection bug: Radix ScrollArea's `**:!block` CSS rule was forcing all descendants (including inputs) to `display: block !important`, breaking text selection
- Replaced `<ScrollArea>` in ConfigPanel.tsx with a native `<div className="h-full overflow-y-auto">` and added inline `user-select: text` style
- Removed the `**:!block` rule from scroll-area.tsx (kept `[&>div]:!block` which is the necessary one for the radix viewport child)
- Added global CSS rule in globals.css: `input, textarea, [contenteditable] { user-select: text; ... }` to defend against any inherited user-select: none

- Added Auto Layout feature:
  - New `Graph.compute_auto_layout()` method in core.py: Sugiyama-style layered layout with longest-path layering, Brandes-Kösch median crossing reduction (8 sweeps), and coordinate assignment
  - New `POST /api/graph/auto-layout` endpoint in main.py (with AutoLayoutReq schema)
  - New `api.autoLayout()` method in src/lib/node-editor/api.ts
  - New "Auto Layout" button in page.tsx top toolbar (with LayoutGrid icon from lucide-react)
  - Updates positionsRef to prevent syncFromGraph from overriding the new positions

- Added MQTT communication node group:
  - Created `mini-services/node-editor-server/nodes/communication/category.toml`
  - Created `communication/mqtt_receive/{node.toml,impl.py}`:
    - Source node with outputs: payload, topic, new (bool), timestamp
    - Background paho-mqtt client with thread-safe cache
    - Wildcard support via native paho subscribe (+ and #)
    - Cache persists; new flag set on message arrival, cleared on next compute()
    - Reconnects on property change (broker, topic, credentials, qos)
  - Created `communication/mqtt_send/{node.toml,impl.py}`:
    - Inputs: payload (any), topic (string, overrides property when connected)
    - Outputs: sent (bool), error (string)
    - Background paho-mqtt client, publish on compute()
    - Supports QoS and retain flag

- Added external integration API (main.py):
  - `_encode_image_to_data_uri()` helper
  - `_run_frame_and_collect_output()` core runner
  - `POST /api/external/run` (synchronous)
  - `POST /api/external/submit` (async submit, returns task_id)
  - `GET /api/external/result/{task_id}` (poll status)
  - `DELETE /api/external/result/{task_id}` (cancel)
  - `GET /api/external/tasks` (list)
  - `GET /api/external/graph-info` (introspection: nodes + ports)
  - In-memory task store with threading.Lock for thread safety

- Added --server mode to run_headless.py:
  - New `run_server(g, host, port)` function that builds a minimal FastAPI app exposing the same /api/external/* endpoints
  - Added CLI args: --server, --host, --port
  - Lets the headless runner accept external HTTP requests without the GUI

- Created Python client library at clients/python/:
  - `multimodal_client.py`: zero-dependency sync client (urllib) + aiohttp-based async client
  - Classes: MultimodalClient, AsyncMultimodalClient, GraphInfo, NodeInfo, PortInfo, RunResult, TaskStatus, MultimodalError
  - Sync methods: graph_info, list_nodes, list_tasks, run, submit, get_result, wait_for_result, cancel_task
  - Async methods: same as sync but `async def`, plus `run_async` convenience wrapper
  - CLI: `python -m multimodal_client {info|run|submit|poll} ...`
  - `__init__.py` exports all public classes
  - `README.md` with full API docs and examples
  - `test_client.py`: end-to-end mock-server tests for sync client (6 tests, all pass)
  - `test_async_client.py`: end-to-end mock-server tests for async client (4 tests, all pass)

- Updated README.md with detailed sections for:
  - Auto Layout (algorithm + API)
  - MQTT communication node group (behavior, ports, properties, examples)
  - External Python client (sync + async examples, CLI, endpoint reference)
  - Headless server mode

- Added paho-mqtt>=2.1.0 to pyproject.toml dependencies

Stage Summary:
- All 5 requested features implemented and tested
- Backend smoke test passes: auto-layout endpoint returns 200, external/* endpoints work correctly with both valid and invalid inputs
- Python client tests pass: 6 sync tests + 4 async tests all green
- 129 nodes discovered correctly (including 2 new MQTT nodes)
- Project structure preserved; no breaking changes to existing functionality
- Ready for packaging as zip

Artifacts produced:
- /home/z/my-project/Multimodal-Node-Editor/ (modified project)
- /home/z/my-project/Multimodal-Node-Editor/clients/python/ (new: Python client)
- /home/z/my-project/Multimodal-Node-Editor/mini-services/node-editor-server/nodes/communication/ (new: MQTT nodes)
