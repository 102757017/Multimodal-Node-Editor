"""
main.py - FastAPI server for the VisionMaster-like node editor (GUI backend).

Exposes a REST API for graph manipulation, dynamic ports, cross-level
ComboBox sources, source-data pushing, sharded execution, and save/load.

In addition to the browser-UI endpoints, this server ALSO exposes the
/api/external/* HTTP endpoints so that the Python client library
(multimodal_client.HttpClient) can push images and read outputs while
the GUI is running.  This lets you use the same Python client against:

  - the GUI backend (this file, FastAPI on port 3030) — for development /
    interactive use where you want to see the graph in the browser while
    a script feeds it images.
  - the headless backend (run_headless.py --server) — for production /
    server deployments where no browser is needed.  The headless backend
    uses shared-memory transport (zero-copy) which is more efficient, but
    the HTTP API here is convenient when the GUI is already running.

For maximum efficiency (zero-serialisation), import HeadlessController
directly in-process — see headless_api.py.

Run with:  python -m uvicorn main:app --host 0.0.0.0 --port 3030 --reload
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File as FileParam
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import node_def
from core import ExecutionResult, Graph
from models import Connection, Node, Port, generate_id
from discovery import get_category_tree, StubCompute
from model_registry import registry
# HeadlessController provides the in-process implementation of the external
# API (run / submit / get_result / etc.).  We wrap its methods in HTTP
# endpoints below so the Python HttpClient client can talk to the GUI
# backend the same way it talks to the headless backend.
from headless_api import HeadlessController

app = FastAPI(title="Multimodal Node Editor (refactored)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Global graph (single-session demo)
# ---------------------------------------------------------------------------
graph = Graph(sync_timeout=5.0, max_frames=8)


# ===========================================================================
# Request / response schemas
# ===========================================================================
class AddNodeReq(BaseModel):
    definition_id: str
    version: Optional[str] = None
    name: Optional[str] = None
    position: Optional[Dict[str, float]] = None


class AddConnectionReq(BaseModel):
    from_node_id: str
    from_port_id: str
    to_node_id: str
    to_port_id: str


class SetPropertiesReq(BaseModel):
    properties: Dict[str, Any]


class SetInputSourceReq(BaseModel):
    port_name: str
    source: Optional[str] = None  # "node_id.port_name" or null to clear


class SetTriggerModeReq(BaseModel):
    mode: str  # "ALL" | "ANY"


class SetPositionReq(BaseModel):
    position: Dict[str, float]


class AddDynamicPortReq(BaseModel):
    group_name: str


class RenamePortReq(BaseModel):
    display_name: str


class SourceDataReq(BaseModel):
    data: Dict[str, Any]


class ExecuteStepReq(BaseModel):
    context: Optional[Dict[str, Any]] = None


class SaveGraphReq(BaseModel):
    path: Optional[str] = None


class LoadGraphReq(BaseModel):
    path: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# ===========================================================================
# Node definitions
# ===========================================================================
@app.get("/api/nodes")
def list_nodes():
    defs = node_def.list_node_definitions()
    return {
        "nodes": [
            {
                "definition_id": d.definition_id,
                "version": d.version,
                "display_name": d.display_name,
                "description": d.description,
                "order": d.order,
                "category": d.category,
                "is_source_node": d.is_source_node,
                "measure_time": d.measure_time,
                "available": not isinstance(d.compute_logic, StubCompute),
                "inputs": [p.model_dump(mode="json") for p in d.inputs],
                "outputs": [p.model_dump(mode="json") for p in d.outputs],
                "properties": [pd.model_dump(mode="json") for pd in d.properties],
                "dynamic_port_configs": {
                    k: v.model_dump(mode="json") for k, v in d.dynamic_port_configs.items()
                },
            }
            for d in defs
        ],
        "categories": get_category_tree(),
    }


# ===========================================================================
# Graph CRUD
# ===========================================================================
@app.get("/api/graph")
def get_graph():
    return graph.to_dict()


@app.post("/api/graph/nodes")
def add_node(req: AddNodeReq):
    try:
        definition = node_def.get_node_definition(req.definition_id, req.version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    node = definition.create_node(name=req.name, position=req.position)
    graph.add_node(node)
    return node.model_dump(mode="json")


@app.delete("/api/graph/nodes/{node_id}")
def delete_node(node_id: str):
    if not graph.remove_node(node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"ok": True}


@app.post("/api/graph/connections")
def add_connection(req: AddConnectionReq):
    conn = Connection(
        from_node_id=req.from_node_id,
        from_port_id=req.from_port_id,
        to_node_id=req.to_node_id,
        to_port_id=req.to_port_id,
    )
    ok, err = graph.add_connection(conn)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    # auto-expand dynamic ports if needed
    graph.maybe_auto_expand(req.to_node_id, req.to_port_id)
    return conn.model_dump(mode="json")


@app.delete("/api/graph/connections/{conn_id}")
def delete_connection(conn_id: str):
    if not graph.remove_connection(conn_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"ok": True}


@app.put("/api/graph/nodes/{node_id}/properties")
def set_properties(node_id: str, req: SetPropertiesReq):
    node = graph._find_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    # clamp numeric property values to their min/max from the definition
    try:
        definition = node_def.get_node_definition(node.definition_id, node.definition_version)
        prop_defs = {pd.name: pd for pd in definition.properties}
    except Exception:
        prop_defs = {}
    clamped = {}
    for k, v in req.properties.items():
        pd = prop_defs.get(k)
        if pd and pd.type in ("int", "float") and isinstance(v, (int, float)):
            if pd.min is not None:
                v = max(pd.min, v)
            if pd.max is not None:
                v = min(pd.max, v)
            if pd.type == "int":
                v = int(v)
        clamped[k] = v
    graph.set_node_properties(node_id, clamped)
    return {"ok": True}


@app.put("/api/graph/nodes/{node_id}/trigger-mode")
def set_trigger_mode(node_id: str, req: SetTriggerModeReq):
    if not graph.set_trigger_mode(node_id, req.mode):
        raise HTTPException(status_code=400, detail="Invalid node or mode")
    return {"ok": True}


@app.put("/api/graph/nodes/{node_id}/position")
def set_position(node_id: str, req: SetPositionReq):
    """Persist a node's canvas position so Save/Load/refresh preserve the layout."""
    node = graph._find_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    node.position = {"x": float(req.position.get("x", 0)), "y": float(req.position.get("y", 0))}
    return {"ok": True}


class AutoLayoutReq(BaseModel):
    """Optional parameters for the auto-layout algorithm."""
    direction: str = "LR"  # "LR" (left-to-right) or "TB" (top-to-bottom)
    node_width: int = 220
    node_height: int = 120
    layer_gap: int = 80
    node_gap: int = 40


@app.post("/api/graph/auto-layout")
def auto_layout(req: AutoLayoutReq):
    """Auto-arrange all nodes using a layered (Sugiyama-style) layout.

    Computes new positions based on the connection graph's topology and
    applies them to every node.  Returns the new positions keyed by node id
    so the client can update its local state.
    """
    positions = graph.compute_auto_layout(
        direction=req.direction,
        node_width=req.node_width,
        node_height=req.node_height,
        layer_gap=req.layer_gap,
        node_gap=req.node_gap,
    )
    # persist positions on the backend nodes
    for nid, pos in positions.items():
        node = graph._find_node(nid)
        if node:
            node.position = {"x": float(pos["x"]), "y": float(pos["y"])}
    return {"ok": True, "positions": positions}


@app.put("/api/graph/nodes/{node_id}/input-source")
def set_input_source(node_id: str, req: SetInputSourceReq):
    if not graph.set_input_source(node_id, req.port_name, req.source):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"ok": True}


@app.get("/api/graph/nodes/{node_id}/combobox/{port_name}")
def get_combobox_candidates(node_id: str, port_name: str):
    return {"candidates": graph.get_combobox_candidates(node_id, port_name)}


# ===========================================================================
# Dynamic ports
# ===========================================================================
@app.post("/api/graph/nodes/{node_id}/dynamic-port")
def add_dynamic_port(node_id: str, req: AddDynamicPortReq):
    port = graph.add_dynamic_port(node_id, req.group_name)
    if port is None:
        raise HTTPException(status_code=400, detail="Cannot add dynamic port (max reached or not found)")
    return port.model_dump(mode="json")


@app.delete("/api/graph/nodes/{node_id}/dynamic-port/{port_id}")
def remove_dynamic_port(node_id: str, port_id: str):
    ok, err = graph.remove_dynamic_port(node_id, port_id)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    # return updated node
    node = graph._find_node(node_id)
    return node.model_dump(mode="json") if node else {"ok": True}


@app.put("/api/graph/nodes/{node_id}/port/{port_id}/rename")
def rename_port(node_id: str, port_id: str, req: RenamePortReq):
    ok, err = graph.rename_port(node_id, port_id, req.display_name)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True}


# ===========================================================================
# Execution control
# ===========================================================================
@app.post("/api/graph/source-data/{node_id}")
def push_source_data(node_id: str, req: SourceDataReq):
    node = graph._find_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    graph.set_source_data(node_id, req.data)
    return {"ok": True, "frame_id": graph.exec_state.frame_id}


@app.post("/api/graph/start-frame")
def start_frame():
    """Begin a new frame.  Resets per-node execution flags."""
    graph.start_frame()
    return {"ok": True, "frame_id": graph.exec_state.frame_id}


@app.post("/api/graph/mark-depleted/{node_id}")
def mark_depleted(node_id: str):
    node = graph._find_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    graph.mark_source_depleted(node_id)
    return {"ok": True}


@app.post("/api/graph/execute-step")
def execute_step(req: ExecuteStepReq):
    res = graph.execute_step(req.context)
    return _result_to_dict(res)


@app.post("/api/graph/reset-frame")
def reset_frame():
    graph.reset_frame_state()
    return {"ok": True}


@app.get("/api/graph/status")
def get_status():
    st = graph.exec_state
    return {
        "frame_id": st.frame_id,
        "source_depleted": st.source_depleted,
        "all_sources_depleted": graph._all_sources_depleted(),
        "sync_timeout": st.sync_timeout,
        "node_count": len(graph.nodes),
        "connection_count": len(graph.connections),
    }


# ===========================================================================
# Save / load
# ===========================================================================
SAVE_DIR = Path(__file__).parent / "saves"
SAVE_DIR.mkdir(exist_ok=True)


@app.post("/api/graph/save")
def save_graph(req: SaveGraphReq):
    path = Path(req.path) if req.path else SAVE_DIR / f"graph-{int(time.time())}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = graph.to_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return {"ok": True, "path": str(path)}


@app.post("/api/graph/load")
def load_graph(req: LoadGraphReq):
    global graph
    data = req.data
    if req.path:
        with open(req.path, "r", encoding="utf-8") as f:
            data = json.load(f)
    if not data:
        raise HTTPException(status_code=400, detail="No data or path provided")
    # rebuild graph
    new_graph = Graph(sync_timeout=graph.sync_timeout, max_frames=graph.exec_state.max_frames)
    new_graph.id = data.get("id", generate_id("graph"))
    new_graph.graph_format_version = data.get("graph_format_version", "1.0.0")
    for n_data in data.get("nodes", []):
        node = Node(**n_data)
        new_graph.add_node(node)
    for c_data in data.get("connections", []):
        conn = Connection(**c_data)
        new_graph.connections.append(conn)
    new_graph._mark_dirty()
    # replace global
    graph.__dict__.update(new_graph.__dict__)
    return {"ok": True, "graph": graph.to_dict()}


@app.get("/api/graph/saves")
def list_saves():
    files = sorted(SAVE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"saves": [{"name": p.name, "path": str(p)} for p in files]}


# ===========================================================================
# Helpers
# ===========================================================================
def _result_to_dict(res: ExecutionResult) -> Dict[str, Any]:
    return {
        "status": res.status,
        "frame_id": res.frame_id,
        "executed_nodes": res.executed_nodes,
        "skipped_nodes": res.skipped_nodes,
        "waiting_nodes": res.waiting_nodes,
        "outputs": res.outputs,
        "errors": res.errors,
        "node_times": res.node_times,
        "elapsed_ms": res.elapsed_ms,
    }


# ===========================================================================
# Global model registry management
# ===========================================================================
class PreloadModelReq(BaseModel):
    key: str
    label: str = ""
    est_bytes: int = 0
    # The loader is identified by a registered loader name + args (JSON).
    # Registered loaders are added via register_model_loader().
    loader_name: str
    loader_args: Dict[str, Any] = {}


# Registry of named loaders that can be invoked from the UI.
# A loader is a function (args: dict) -> model instance.
# Nodes register their model loaders here so the UI can preload them.
_model_loaders: Dict[str, Callable[[Dict[str, Any]], Any]] = {}


def register_model_loader(name: str, fn: Callable[[Dict[str, Any]], Any]):
    """Register a named model loader so it can be invoked from the UI."""
    _model_loaders[name] = fn


@app.get("/api/model-loaders")
def list_model_loaders():
    """List registered model loaders (for the UI's preload dialog)."""
    return {"loaders": [{"name": k} for k in _model_loaders.keys()]}


@app.post("/api/models/preload")
def preload_model(req: PreloadModelReq):
    """Preload a model by invoking a registered loader.  The model is then
    cached in the global registry under `key`."""
    loader_fn = _model_loaders.get(req.loader_name)
    if loader_fn is None:
        raise HTTPException(status_code=404, detail=f"Loader '{req.loader_name}' not registered")
    def _loader():
        return loader_fn(req.loader_args)
    ok, err = registry.preload(req.key, _loader, est_bytes=req.est_bytes, label=req.label or req.loader_name)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "snapshot": registry.snapshot()}


@app.get("/api/models")
def list_models():
    """Return a snapshot of the global model cache (loaded models, memory, hits)."""
    return registry.snapshot()


@app.delete("/api/models/{key}")
def unload_model(key: str):
    """Unload a single model from the cache (frees its memory)."""
    ok = registry.unload(key)
    if not ok:
        raise HTTPException(status_code=404, detail="Model key not found")
    return {"ok": True, "snapshot": registry.snapshot()}


@app.delete("/api/models")
def unload_all_models():
    """Unload every cached model."""
    registry.clear()
    return {"ok": True, "snapshot": registry.snapshot()}


# ===========================================================================
# File upload (for file_picker properties — matches the original project)
#
# The browser cannot expose a file's real local path for security reasons, so
# the original project uploads the file to a temp directory and returns the
# absolute path of the stored copy.  Nodes then read the file from that path.
# This endpoint also returns a preview image (first frame for videos, the
# image itself for images) so the UI can show a thumbnail immediately.
# ===========================================================================
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


@app.post("/api/upload")
async def upload_file(file: UploadFile = FileParam(...)):
    """Upload a file, store it locally, return the absolute path + preview."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    safe_name = Path(file.filename).name
    dest = UPLOAD_DIR / safe_name
    # avoid collision
    counter = 1
    while dest.exists():
        dest = UPLOAD_DIR / f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
        counter += 1
    # write in chunks to handle large files
    total = 0
    with open(dest, "wb") as buf:
        while chunk := await file.read(1024 * 1024):
            buf.write(chunk)
            total += len(chunk)

    result: Dict[str, Any] = {"path": str(dest)}
    suffix = dest.suffix.lower()
    video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}

    if suffix in image_exts:
        # return the image as a base64 data URI for instant preview
        try:
            import base64
            data = dest.read_bytes()
            mime = f"image/{suffix[1:]}" if suffix != ".jpg" else "image/jpeg"
            result["first_frame"] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:
            pass
    elif suffix in video_exts:
        # extract first frame
        try:
            import cv2
            import numpy as np
            cap = cv2.VideoCapture(str(dest))
            if cap.isOpened():
                ret, frame = cap.read()
                if ret:
                    import base64, io
                    from PIL import Image
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    img.thumbnail((960, 960))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    result["first_frame"] = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
                fc = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if fc > 0:
                    result["frame_count"] = fc
                cap.release()
        except Exception:
            pass

    return result


@app.get("/")
def root():
    return {"name": "node-editor-server", "status": "ok"}


# ===========================================================================
# External integration API (HTTP)
#
# These endpoints let an external Python process (or any HTTP client) push an
# image into a designated Image node, run the graph for one frame, and read
# back the output of any node — while the GUI is running.
#
# The same Python client (multimodal_client.HttpClient) works against:
#   - this GUI backend (FastAPI on port 3030)
#   - the headless backend (run_headless.py --server, shared-memory transport)
#
# All logic is delegated to HeadlessController (from headless_api.py) so there
# is a single implementation shared between the in-process, shared-memory and
# HTTP transports.
#
# Two execution modes:
#   * Synchronous:  POST /api/external/run          (blocks until the frame
#                   finishes and returns the requested output)
#   * Asynchronous: POST /api/external/submit       (returns a task_id
#                   immediately) + GET /api/external/result/{task_id}
#                   (poll for completion / output)
# ===========================================================================

# A HeadlessController wrapping the global graph.  All /api/external/*
# endpoints delegate to this controller so the logic stays in one place.
_external_ctrl = HeadlessController(graph)


class ExternalRunReq(BaseModel):
    """Push an image into an Image node and run one frame."""
    image_node_id: str
    image_port_name: str = "image_out"
    # image can be supplied as:
    #   - a base64 data URI ("data:image/jpeg;base64,...")
    #   - raw base64 (we'll wrap it as jpeg)
    #   - a file path on the server (if image_path is set instead)
    image_base64: Optional[str] = None
    image_path: Optional[str] = None
    output_node_id: str
    output_port_name: str
    max_steps: int = 50
    reset_frame: bool = True


class ExternalSubmitReq(ExternalRunReq):
    """Same shape as ExternalRunReq but returns immediately with a task id."""
    pass


def _external_error_to_http(e: Exception):
    """Translate controller exceptions to HTTPException with appropriate codes."""
    if isinstance(e, (ValueError, FileNotFoundError, TypeError)):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, KeyError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, TimeoutError):
        return HTTPException(status_code=504, detail=str(e))
    return HTTPException(status_code=500, detail=str(e))


@app.get("/api/external/graph-info")
def external_graph_info():
    """Return a compact summary of the loaded graph so external clients can
    discover node ids, port names, and data types without parsing the full
    graph dump."""
    return _external_ctrl.graph_info()


@app.post("/api/external/run")
def external_run(req: ExternalRunReq):
    """Synchronous: push image, run one frame, return the requested output."""
    try:
        result = _external_ctrl.run(
            image_node_id=req.image_node_id,
            image_port_name=req.image_port_name,
            image_base64=req.image_base64,
            image_path=req.image_path,
            output_node_id=req.output_node_id,
            output_port_name=req.output_port_name,
            max_steps=req.max_steps,
            reset_frame=req.reset_frame,
        )
        return result
    except Exception as e:
        raise _external_error_to_http(e)


@app.post("/api/external/submit")
def external_submit(req: ExternalSubmitReq):
    """Asynchronous: queue a run and return a task_id immediately.

    Poll the result with GET /api/external/result/{task_id}.
    """
    try:
        task_id = _external_ctrl.submit(
            image_node_id=req.image_node_id,
            image_port_name=req.image_port_name,
            image_base64=req.image_base64,
            image_path=req.image_path,
            output_node_id=req.output_node_id,
            output_port_name=req.output_port_name,
            max_steps=req.max_steps,
            reset_frame=req.reset_frame,
        )
        return {"task_id": task_id, "status": "pending"}
    except Exception as e:
        raise _external_error_to_http(e)


@app.get("/api/external/result/{task_id}")
def external_result(task_id: str):
    """Poll the status / output of an asynchronously submitted run."""
    try:
        return _external_ctrl.get_result(task_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/external/result/{task_id}")
def external_cancel(task_id: str):
    """Delete a task from the in-memory store (best-effort cleanup)."""
    ok = _external_ctrl.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return {"ok": True}


@app.get("/api/external/tasks")
def external_list_tasks():
    """List all known async tasks (most recent first)."""
    return {"tasks": _external_ctrl.list_tasks()}


@app.get("/api/external/last-result")
def external_last_result():
    """Return the last external run's result (for browser UI polling).

    The browser polls this endpoint to detect when an external Python script
    has pushed an image and run the graph.  When a new result is detected
    (``seq`` increments), the browser updates its node previews to show the
    externally-produced outputs.

    Returns ``{"seq": 0, "result": None}`` if no external run has happened.
    """
    return _external_ctrl.get_last_result()


@app.get("/api/external/ping")
def external_ping():
    """Health check — distinguishes the GUI backend from the headless backend.

    Returns ``{"ok": true, "mode": "gui-http"}`` for the GUI backend.
    The headless shared-memory server returns ``{"mode": "headless-shm"}``.
    """
    return {"ok": True, "mode": "gui-http"}

