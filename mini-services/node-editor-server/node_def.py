"""
node_def.py - Node definition registry with dynamic_ports config parsing.

Nodes are defined declaratively (TOML-like dicts in code) and registered at
import time.  Each definition may declare one or more dynamic port groups via
`dynamic_ports.inputs` / `dynamic_ports.outputs`; those configs are copied onto
every Node instance created from the definition.
"""
from __future__ import annotations

import base64
import io
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from models import DynamicPortConfig, Node, Port, PropertyDefinition


# ---------------------------------------------------------------------------
# Compute base
# ---------------------------------------------------------------------------
class ComputeLogic:
    """Base class for node compute logic.  Override `compute`.

    Also provides the cancel/reset helpers that upstream nodes rely on, and
    a global model-cache helper so that multiple nodes sharing the same large
    model (CLIP, LLM, ONNX session, …) only load ONE copy.
    """

    def compute(
        self,
        inputs: Dict[str, Any],
        properties: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # -- cancel support (used by upstream nodes) ---------------------------
    def _get_cancel_event(self):
        if not hasattr(self, "_cancel_event"):
            import threading
            self._cancel_event = threading.Event()
        return self._cancel_event

    def request_cancel(self):
        self._get_cancel_event().set()

    def is_cancelled(self) -> bool:
        return self._get_cancel_event().is_set()

    def clear_cancel(self):
        self._get_cancel_event().clear()

    def reset(self):
        """Reset per-node state.  Override in streaming nodes."""
        pass

    # -- global model cache ------------------------------------------------
    def _get_cached_model(
        self,
        key: str,
        loader: Callable[[], Any],
        *,
        est_bytes: int = 0,
        label: str = "",
    ) -> Tuple[Any, Optional[str]]:
        """Fetch a shared model instance from the global registry.

        ``loader`` is called at most once per ``key``; every subsequent call
        returns the cached instance.  Use a stable key that uniquely
        identifies the model variant, e.g.::

            self._get_cached_model(
                f"onnx:{model_path}:gpu={use_gpu}",
                lambda: onnxruntime.InferenceSession(model_path, providers=...),
                label="onnx",
            )

        Returns ``(model, None)`` on success or ``(None, error_message)`` if
        the loader raised.
        """
        from model_registry import registry
        return registry.get(key, loader, est_bytes=est_bytes, label=label)


# ---------------------------------------------------------------------------
# Node definition
# ---------------------------------------------------------------------------
class NodeDefinition:
    """Metadata + compute logic for a node type."""

    def __init__(
        self,
        definition_id: str,
        version: str,
        display_name: str,
        description: str = "",
        order: int = 100,
        category: str = "general",
        measure_time: bool = True,
        inputs: Optional[List[Port]] = None,
        outputs: Optional[List[Port]] = None,
        properties: Optional[List[PropertyDefinition]] = None,
        dynamic_port_configs: Optional[Dict[str, DynamicPortConfig]] = None,
        compute_logic: Optional[ComputeLogic] = None,
        is_source_node: Optional[bool] = None,
    ):
        self.definition_id = definition_id
        self.version = version
        self.display_name = display_name
        self.description = description
        self.order = order
        self.category = category
        self.measure_time = measure_time
        self.inputs = inputs or []
        self.outputs = outputs or []
        self.properties = properties or []
        self.dynamic_port_configs = dynamic_port_configs or {}
        self.compute_logic = compute_logic
        self.is_source_node = is_source_node  # None => infer from inputs

    # -- factory -----------------------------------------------------------
    def create_node(self, name: Optional[str] = None, position: Optional[Dict[str, float]] = None) -> Node:
        """Create a Node instance from this definition, copying ports, default
        properties and dynamic_port_configs."""
        # Deep-ish copy of ports (new ids)
        def clone_port(p: Port, direction: str) -> Port:
            return Port(
                name=p.name,
                display_name=p.display_name,
                data_type=p.data_type,
                direction=direction,
                preview=p.preview,
                metadata=dict(p.metadata),
            )

        inputs = [clone_port(p, "in") for p in self.inputs]
        outputs = [clone_port(p, "out") for p in self.outputs]

        # Default properties
        props: Dict[str, Any] = {}
        for pd in self.properties:
            props[pd.name] = pd.default

        # Seed min_count dynamic ports
        dyn_configs = {k: v.model_copy() for k, v in self.dynamic_port_configs.items()}
        for group_name, cfg in dyn_configs.items():
            for i in range(cfg.min_count):
                _add_dynamic_port_to_lists(inputs, outputs, cfg, i)

        node = Node(
            definition_id=self.definition_id,
            definition_version=self.version,
            name=name or self.display_name,
            inputs=inputs,
            outputs=outputs,
            properties=props,
            position=position or {"x": 0.0, "y": 0.0},
            trigger_mode="ALL",
            dynamic_port_configs=dyn_configs,
            is_source_node=self.is_source_node,
        )
        return node

    def compute(
        self,
        inputs: Dict[str, Any],
        properties: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.compute_logic is None:
            return {}
        return self.compute_logic.compute(inputs, properties, context or {})


# ---------------------------------------------------------------------------
# Helpers for dynamic port creation
# ---------------------------------------------------------------------------
def _dynamic_port_name(prefix: str, index: int) -> str:
    """Internal template name.  Index is 1-based."""
    return f"{prefix} {index}"


def _add_dynamic_port_to_lists(
    inputs: List[Port],
    outputs: List[Port],
    cfg: DynamicPortConfig,
    index: int,  # 0-based
) -> Port:
    """Append a new dynamic port (index-th, 1-based display) to the right list."""
    display_index = index + 1
    port = Port(
        name=_dynamic_port_name(cfg.prefix, display_index),
        display_name=_dynamic_port_name(cfg.prefix, display_index),
        data_type=cfg.data_type,
        direction=cfg.direction,
        preview=cfg.preview,
        metadata={
            "is_dynamic": True,
            "dynamic_group": cfg.group_name,
            "dynamic_index": display_index,
        },
    )
    if cfg.direction in ("in", "inout"):
        inputs.append(port)
    if cfg.direction in ("out", "inout"):
        outputs.append(port)
    return port


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_registry: Dict[Tuple[str, str], NodeDefinition] = {}
_registry_lock = threading.Lock()


def register_node(node_def: NodeDefinition):
    with _registry_lock:
        _registry[(node_def.definition_id, node_def.version)] = node_def


def get_node_definition(definition_id: str, version: Optional[str] = None) -> NodeDefinition:
    with _registry_lock:
        if version:
            key = (definition_id, version)
            if key not in _registry:
                raise ValueError(f"Node definition {key} not found.")
            return _registry[key]
        # keys are (definition_id, version); collect all matching definition_id
        matches = [(ver, nd) for (did, ver), nd in _registry.items() if did == definition_id]
        if not matches:
            raise ValueError(f"Node definition '{definition_id}' not found.")
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[0][1]


def list_node_definitions() -> List[NodeDefinition]:
    with _registry_lock:
        # latest version per definition_id
        seen: Dict[str, NodeDefinition] = {}
        for (did, _ver), d in _registry.items():
            if did not in seen or d.version > seen[did].version:
                seen[did] = d
        return sorted(seen.values(), key=lambda d: (d.category, d.order, d.definition_id))


def list_categories() -> List[Dict[str, Any]]:
    cats: Dict[str, Dict[str, Any]] = {}
    for d in list_node_definitions():
        if d.category not in cats:
            cats[d.category] = {"category_id": d.category, "display_name": d.category.title(), "order": 0}
    return sorted(cats.values(), key=lambda c: c["category_id"])


# ===========================================================================
# Built-in node implementations
# ===========================================================================
class ValueSourceCompute(ComputeLogic):
    """source.value - emits a configurable float.  Source node."""

    def compute(self, inputs, properties, context=None):
        # If external source data was pushed, it overrides the property.
        pushed = (context or {}).get("source_data")
        val = pushed if pushed is not None else float(properties.get("value", 0.0))
        return {"value": val, "__frame_count__": 1}


class ColorImageSourceCompute(ComputeLogic):
    """source.color_image - emits a solid-color image as a numpy array.  Source node."""

    def compute(self, inputs, properties, context=None):
        try:
            import numpy as np
            from PIL import Image
        except Exception:
            transparent = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ"
                "/pLvAAAAAElFTkSuQmCC"
            )
            return {"image": transparent, "__frame_count__": 1}

        color = properties.get("color", "#3b82f6").lstrip("#")
        w = int(properties.get("width", 256))
        h = int(properties.get("height", 256))
        if len(color) == 3:
            color = "".join(c * 2 for c in color)
        try:
            rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            rgb = (59, 130, 246)
        # produce numpy array in BGR order (OpenCV convention) — original
        # nodes use cv2 and expect BGR; _numpy_to_base64 also expects BGR.
        bgr = (rgb[2], rgb[1], rgb[0])
        arr = np.full((h, w, 3), bgr, dtype=np.uint8)
        return {"image": arr, "__frame_count__": 1}


class AddCompute(ComputeLogic):
    def compute(self, inputs, properties, context=None):
        a = inputs.get("a", 0.0) or 0.0
        b = inputs.get("b", 0.0) or 0.0
        return {"result": float(a) + float(b)}


class MultiplyCompute(ComputeLogic):
    def compute(self, inputs, properties, context=None):
        a = inputs.get("a", 0.0) or 0.0
        b = inputs.get("b", 0.0) or 0.0
        return {"result": float(a) * float(b)}


class DynamicSumCompute(ComputeLogic):
    """math.dynamic_sum - sums every connected dynamic 'Value N' input."""

    def compute(self, inputs, properties, context=None):
        total = 0.0
        for key, val in inputs.items():
            if val is None:
                continue
            try:
                total += float(val)
            except (TypeError, ValueError):
                pass
        return {"result": total, "count": len([v for v in inputs.values() if v is not None])}


class NumberDisplayCompute(ComputeLogic):
    """display.number - shows the latest numeric value."""

    def compute(self, inputs, properties, context=None):
        val = inputs.get("value")
        try:
            val = float(val) if val is not None else None
        except (TypeError, ValueError):
            pass
        return {"value": val, "__display_text__": f"{val:.4f}" if isinstance(val, float) else str(val)}


# ===========================================================================
# Register built-in nodes
# ===========================================================================
def _register_all():
    register_node(NodeDefinition(
        definition_id="source.value",
        version="1.0.0",
        display_name="Value Source",
        description="Emits a configurable float. Source node.",
        order=10,
        category="source",
        is_source_node=True,
        inputs=[],
        outputs=[Port(name="value", display_name="Value", data_type="float", direction="out", preview=False)],
        properties=[
            PropertyDefinition(name="value", display_name="Value", type="float", default=1.0,
                               widget="number_input", min=-1000, max=1000, step=0.1),
        ],
        compute_logic=ValueSourceCompute(),
    ))

    register_node(NodeDefinition(
        definition_id="source.color_image",
        version="1.0.0",
        display_name="Color Image",
        description="Emits a solid-color image. Source node.",
        order=20,
        category="source",
        is_source_node=True,
        inputs=[],
        outputs=[Port(name="image", display_name="Image", data_type="image", direction="out", preview=True)],
        properties=[
            PropertyDefinition(name="color", display_name="Color", type="string", default="#3b82f6",
                               widget="color"),
            PropertyDefinition(name="width", display_name="Width", type="int", default=256,
                               widget="number_input", min=16, max=1024, step=16),
            PropertyDefinition(name="height", display_name="Height", type="int", default=256,
                               widget="number_input", min=16, max=1024, step=16),
        ],
        compute_logic=ColorImageSourceCompute(),
    ))

    register_node(NodeDefinition(
        definition_id="math.add",
        version="1.0.0",
        display_name="Add",
        description="a + b -> result",
        order=10,
        category="math",
        inputs=[
            Port(name="a", display_name="A", data_type="float", direction="in", preview=False),
            Port(name="b", display_name="B", data_type="float", direction="in", preview=False),
        ],
        outputs=[Port(name="result", display_name="Result", data_type="float", direction="out", preview=False)],
        properties=[],
        compute_logic=AddCompute(),
    ))

    register_node(NodeDefinition(
        definition_id="math.multiply",
        version="1.0.0",
        display_name="Multiply",
        description="a * b -> result",
        order=20,
        category="math",
        inputs=[
            Port(name="a", display_name="A", data_type="float", direction="in", preview=False),
            Port(name="b", display_name="B", data_type="float", direction="in", preview=False),
        ],
        outputs=[Port(name="result", display_name="Result", data_type="float", direction="out", preview=False)],
        properties=[],
        compute_logic=MultiplyCompute(),
    ))

    register_node(NodeDefinition(
        definition_id="math.dynamic_sum",
        version="1.0.0",
        display_name="Dynamic Sum",
        description="Sums every connected dynamic 'Value N' input (many-to-1 aggregation).",
        order=30,
        category="math",
        inputs=[],  # seeded from dynamic config
        outputs=[
            Port(name="result", display_name="Result", data_type="float", direction="out", preview=False),
            Port(name="count", display_name="Count", data_type="int", direction="out", preview=False),
        ],
        properties=[],
        dynamic_port_configs={
            "value_inputs": DynamicPortConfig(
                group_name="value_inputs",
                prefix="Value",
                data_type="float",
                direction="in",
                min_count=2,
                max_count=8,
                auto_expand=True,
                preview=False,
            ),
        },
        compute_logic=DynamicSumCompute(),
    ))

    register_node(NodeDefinition(
        definition_id="display.number",
        version="1.0.0",
        display_name="Number Display",
        description="Displays the latest numeric input value.",
        order=10,
        category="display",
        inputs=[Port(name="value", display_name="Value", data_type="float", direction="in", preview=False)],
        outputs=[Port(name="value", display_name="Value", data_type="float", direction="out", preview=False)],
        properties=[],
        compute_logic=NumberDisplayCompute(),
    ))


# ===========================================================================
# Shared-model demo nodes (illustrate the global ModelRegistry)
# ===========================================================================
class _SharedMockModel:
    """A pretend large model.  In real usage this would be CLIP / LLM / ONNX.

    Holds a big preallocated buffer so memory savings are visible when the
    instance is shared across nodes."""

    def __init__(self, model_name: str, size_mb: int = 50):
        self.model_name = model_name
        self.size_mb = size_mb
        # allocate a buffer to simulate a heavy model
        try:
            import numpy as np
            self._buffer = np.zeros(size_mb * 1024 * 1024 // 8, dtype=np.float64)
        except Exception:
            self._buffer = [0.0] * (size_mb * 1024 * 128)
        self._instance_id = id(self)

    def predict(self, x: float) -> float:
        """Toy prediction: hash the buffer + input so the result is stable."""
        h = hash((self.model_name, round(x, 6))) & 0xFFFF
        return float(h) / 1000.0


class SharedModelInferenceCompute(ComputeLogic):
    """ai.shared_model_inference - loads a shared mock model and runs inference.

    Drop two of these on the canvas with the SAME model_name — the global
    ModelRegistry ensures only ONE model instance is created, shared by both
    nodes.  Different model_name → separate instance.
    """

    def compute(self, inputs, properties, context=None):
        model_name = str(properties.get("model_name", "demo-v1"))
        size_mb = int(properties.get("size_mb", 50))
        x = float(inputs.get("x", 0.0) or 0.0)
        # cache key encodes everything that distinguishes a model variant
        key = f"mock_model:{model_name}:size={size_mb}"
        model, err = self._get_cached_model(
            key,
            loader=lambda: _SharedMockModel(model_name, size_mb),
            est_bytes=size_mb * 1024 * 1024,
            label="mock_model",
        )
        if err:
            return {"result": 0.0, "model_id": -1, "__error__": err}
        result = model.predict(x)
        return {
            "result": result,
            "model_id": model._instance_id,
            "__display_text__": f"id={model._instance_id} r={result:.3f}",
        }


# register the shared-model demo node
register_node(NodeDefinition(
    definition_id="ai.shared_model_inference",
    version="1.0.0",
    display_name="Shared Model Inference",
    description="Demo: multiple instances share ONE global model via the ModelRegistry. "
                "Drop 2+ of these with the same model_name — they share a single instance.",
    order=5,
    category="ai",
    is_source_node=False,
    inputs=[Port(name="x", display_name="X", data_type="float", direction="in", preview=False)],
    outputs=[
        Port(name="result", display_name="Result", data_type="float", direction="out", preview=False),
        Port(name="model_id", display_name="Model ID", data_type="int", direction="out", preview=False),
    ],
    properties=[
        PropertyDefinition(name="model_name", display_name="Model Name", type="string",
                           default="demo-v1", widget="input"),
        PropertyDefinition(name="size_mb", display_name="Size (MB)", type="int",
                           default=50, widget="number_input", min=1, max=500, step=10),
    ],
    compute_logic=SharedModelInferenceCompute(),
))


_register_all()


# ---------------------------------------------------------------------------
# Discover and register ALL original preset nodes from the cloned project.
# This imports discovery.py which walks the nodes directory and registers
# every node.toml it finds (with stub compute when deps are missing).
# ---------------------------------------------------------------------------
try:
    import discovery  # noqa: F401  (auto-registers on import)
except Exception as _e:  # noqa: BLE001
    print(f"Warning: node discovery failed: {_e}")