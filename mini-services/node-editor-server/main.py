"""
main.py - FastAPI server for the VisionMaster-like node editor.

Exposes a REST API for graph manipulation, dynamic ports, cross-level
ComboBox sources, source-data pushing, sharded execution, and save/load.

Run with:  python -m uvicorn main:app --host 0.0.0.0 --port 3030 --reload
"""
from __future__ import annotations

import json
import time
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
