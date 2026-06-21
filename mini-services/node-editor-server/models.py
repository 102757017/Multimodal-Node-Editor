"""
models.py - Refactored data models for the VisionMaster-like node editor.

Key additions over the original:
  * Port.metadata        : Dict storing dynamic-port markers (is_dynamic, dynamic_group, dynamic_index).
  * DynamicPortConfig    : describes a group of truly dynamic ports (create/delete at runtime).
  * Node.trigger_mode    : "ALL" (default) or "ANY".
  * Node.dynamic_port_configs : Dict[group_name, DynamicPortConfig].
  * Node.is_source_node  : explicit override; defaults to (no input ports).
  * Node.input_sources   : stored inside `properties` as "input_sources" mapping
                           (port_name -> "node_id.port_name") for cross-level ComboBox access.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def generate_id(prefix: str) -> str:
    """Generate a unique id with a human-friendly prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------
class Port(BaseModel):
    """A node input/output port."""
    id: str = Field(default_factory=lambda: generate_id("port"))
    name: str                         # internal name, never changes for dynamic ports
    display_name: Optional[str] = None
    data_type: Any = "any"            # "int","float","image","audio","any",...
    direction: str = "in"             # "in" | "out" | "inout"
    preview: bool = True
    # New: free-form metadata, used to mark dynamic ports.
    # Example: {"is_dynamic": True, "dynamic_group": "image_inputs", "dynamic_index": 2}
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dynamic port group configuration
# ---------------------------------------------------------------------------
class DynamicPortConfig(BaseModel):
    """Declares a group of ports that can be created/deleted at runtime.

    Stored in node.toml as:

        [dynamic_ports.inputs]
        group_name = "image_inputs"
        prefix     = "Image"
        data_type  = "image"
        direction  = "in"
        min_count  = 1
        max_count  = 8
        auto_expand = true
        preview    = true
    """
    group_name: str
    prefix: str                       # template base, e.g. "Image"  -> "Image 1", "Image 2"...
    data_type: Any = "any"
    direction: str = "in"             # "in" | "out"
    min_count: int = 1
    max_count: int = 16
    auto_expand: bool = True          # auto-create next port when last one connects
    preview: bool = True


# ---------------------------------------------------------------------------
# Property definition (lightweight)
# ---------------------------------------------------------------------------
class VisibleWhen(BaseModel):
    """Conditional display: show this property only when `property` has one of `values`."""
    property: str
    values: List[Any] = Field(default_factory=list)


class PropertyDefinition(BaseModel):
    name: str
    display_name: str = ""
    type: str = "float"               # int | float | string | bool | color | ...
    default: Any = None
    widget: str = "input"             # input | number_input | dropdown | checkbox | color | slider | button | file_picker | text_area | ...
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: List[Dict[str, Any]] = Field(default_factory=list)
    # ---- extended fields (matched to the original node.toml schema) ----
    options_source: Optional[str] = None   # dynamic options source (e.g. "cameras")
    accept: Optional[str] = None           # file_picker: accepted file types
    visible_when: Optional[VisibleWhen] = None  # conditional display
    disabled_while_streaming: bool = False
    requires_streaming: bool = False        # button: only enabled while streaming
    requires_gpu: bool = False
    button_label: Optional[str] = None
    requires_api_key: Optional[str] = None  # e.g. "openai"
    rows: Optional[int] = None              # text_area: number of rows
    placeholder: Optional[str] = None       # text_input: placeholder


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------
class Node(BaseModel):
    """A node instance in the graph."""
    id: str = Field(default_factory=lambda: generate_id("node"))
    definition_id: str
    definition_version: Optional[str] = None
    name: str
    inputs: List[Port] = Field(default_factory=list)
    outputs: List[Port] = Field(default_factory=list)
    properties: Dict[str, Any] = Field(default_factory=dict)
    position: Dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})

    # ---- New execution-model fields ----
    # "ALL" : execute once per frame only when every connected input has fresh data.
    # "ANY" : execute whenever any input updates; may run multiple times per frame.
    trigger_mode: str = "ALL"

    # Dynamic port group configs copied from the NodeDefinition at creation time.
    dynamic_port_configs: Dict[str, DynamicPortConfig] = Field(default_factory=dict)

    # Explicit override for source-node detection.  None => inferred (no inputs).
    is_source_node: Optional[bool] = None

    @property
    def effective_is_source(self) -> bool:
        """A node is a source when it has no input ports, unless overridden."""
        if self.is_source_node is not None:
            return self.is_source_node
        return len(self.inputs) == 0

    def get_input_source(self, port_name: str) -> Optional[str]:
        """Return the ComboBox-configured source "node_id.port_name" for an input port,
        or None if not configured."""
        sources = self.properties.get("input_sources", {})
        return sources.get(port_name)

    def set_input_source(self, port_name: str, source: Optional[str]):
        sources = dict(self.properties.get("input_sources", {}))
        if source:
            sources[port_name] = source
        else:
            sources.pop(port_name, None)
        self.properties["input_sources"] = sources


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
class Connection(BaseModel):
    """A wire between an output port and an input port.

    Constraint: an input port may have AT MOST one incoming connection
    (enforced by the graph layer).
    """
    id: str = Field(default_factory=lambda: generate_id("conn"))
    from_node_id: str
    from_port_id: str
    to_node_id: str
    to_port_id: str
