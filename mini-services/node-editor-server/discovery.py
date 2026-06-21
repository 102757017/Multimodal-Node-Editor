"""
discovery.py - Discovers and registers all original preset nodes from
download/multimodal-node-editor/src/nodes/.

Handles:
  * [[ports]] and legacy [[inputs]]/[[outputs]] TOML formats
  * Old-style `dynamic_ports = "Prefix"` (string) → converted to DynamicPortConfig
  * New-style `[dynamic_ports.inputs]` / `[dynamic_ports.outputs]` tables
  * impl.py ComputeLogic loading with graceful fallback to a stub compute
    when dependencies (cv2, torch, aiortc, …) are missing
  * Hierarchical category tree built from category.toml files
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import re
from pathlib import Path

# tomllib is stdlib in Python 3.11+; for 3.10 use the tomli backport.
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from typing import Any, Dict, List, Optional, Tuple

from models import DynamicPortConfig, Port, PropertyDefinition
from node_def import (
    ComputeLogic,
    NodeDefinition,
    register_node,
    _add_dynamic_port_to_lists,
    _dynamic_port_name,
)

# ---------------------------------------------------------------------------
# Node search paths (in priority order).
#
# 1. The local `nodes/` directory next to this file — this is where users
#    place their own nodes (e.g. copied from the original project).
# 2. The original cloned repo's nodes, if present (used in the dev sandbox).
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
NODE_SEARCH_PATHS: list[Path] = [
    _HERE / "nodes",                                  # local user-placed nodes
    _HERE.parent.parent.parent / "download" / "multimodal-node-editor" / "src" / "nodes",  # original repo
]


# ---------------------------------------------------------------------------
# Stub compute (used when impl.py can't be loaded)
# ---------------------------------------------------------------------------
class StubCompute(ComputeLogic):
    """Placeholder compute used when a node's impl.py fails to import."""

    def __init__(self, definition_id: str, reason: str):
        self._definition_id = definition_id
        self._reason = reason

    def compute(self, inputs, properties, context=None):
        return {"__error__": f"Node '{self._definition_id}' unavailable: {self._reason}"}


# ---------------------------------------------------------------------------
# Category tree
# ---------------------------------------------------------------------------
class CategoryNode:
    __slots__ = ("id", "display_name", "order", "default_open", "children")

    def __init__(self, id: str, display_name: str, order: int = 100, default_open: bool = True):
        self.id = id
        self.display_name = display_name
        self.order = order
        self.default_open = default_open
        self.children: List["CategoryNode"] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "order": self.order,
            "default_open": self.default_open,
            "children": [c.to_dict() for c in sorted(self.children, key=lambda c: (c.order, c.display_name))],
        }


_category_tree: Dict[str, CategoryNode] = {}  # id -> CategoryNode


def _ensure_category(cat_id: str, display_name: str = "", order: int = 100, default_open: bool = True) -> CategoryNode:
    if cat_id in _category_tree:
        node = _category_tree[cat_id]
        if display_name:
            node.display_name = display_name
        return node
    node = CategoryNode(cat_id, display_name or cat_id.split(".")[-1].capitalize(), order, default_open)
    _category_tree[cat_id] = node
    # link to parent
    parts = cat_id.split(".")
    if len(parts) > 1:
        parent_id = ".".join(parts[:-1])
        parent = _ensure_category(parent_id)
        parent.children.append(node)
    return node


def get_category_tree() -> List[Dict[str, Any]]:
    roots = [v for v in _category_tree.values() if "." not in v.id]
    return [r.to_dict() for r in sorted(roots, key=lambda c: (c.order, c.display_name))]


# ---------------------------------------------------------------------------
# Port parsing
# ---------------------------------------------------------------------------
def _parse_ports(node_config: dict) -> Tuple[List[Port], List[Port]]:
    """Parse [[ports]] (preferred) or [[inputs]]/[[outputs]] (legacy)."""
    ports_config = node_config.get("ports", [])
    if ports_config:
        inputs, outputs = [], []
        for p in ports_config:
            meta = {}
            # preserve any metadata fields from TOML
            for k in ("is_dynamic", "dynamic_group", "dynamic_index"):
                if k in p:
                    meta[k] = p[k]
            port = Port(
                name=p["name"],
                display_name=p.get("display_name"),
                data_type=p.get("data_type", "any"),
                direction=p.get("direction", "in"),
                preview=p.get("preview", True),
                metadata=meta,
            )
            if port.direction in ("in", "inout"):
                inputs.append(port)
            if port.direction in ("out", "inout"):
                outputs.append(port)
        return inputs, outputs
    # legacy
    inputs = [Port(**p, direction="in") for p in node_config.get("inputs", [])]
    outputs = [Port(**p, direction="out") for p in node_config.get("outputs", [])]
    return inputs, outputs


def _parse_properties(node_config: dict) -> List[PropertyDefinition]:
    result = []
    for p in node_config.get("properties", []):
        try:
            # parse visible_when inline table
            vw = p.get("visible_when")
            visible_when = None
            if vw and isinstance(vw, dict):
                from models import VisibleWhen
                visible_when = VisibleWhen(
                    property=vw.get("property", ""),
                    values=vw.get("values", []),
                )
            result.append(PropertyDefinition(
                name=p["name"],
                display_name=p.get("display_name", p["name"]),
                type=p.get("type", "float"),
                default=p.get("default"),
                widget=p.get("widget", "input"),
                min=p.get("min"),
                max=p.get("max"),
                step=p.get("step"),
                options=p.get("options", []),
                options_source=p.get("options_source"),
                accept=p.get("accept"),
                visible_when=visible_when,
                disabled_while_streaming=p.get("disabled_while_streaming", False),
                requires_streaming=p.get("requires_streaming", False),
                requires_gpu=p.get("requires_gpu", False),
                button_label=p.get("button_label"),
                requires_api_key=p.get("requires_api_key"),
                rows=p.get("rows"),
                placeholder=p.get("placeholder"),
            ))
        except Exception as e:
            print(f"  [warn] property parse failed in {node_config.get('name')}: {p.get('name')} — {e}")
    return result


# ---------------------------------------------------------------------------
# Dynamic port config conversion
# ---------------------------------------------------------------------------
_DYNAMIC_PORT_RE = re.compile(r"^(.+?)\s+(\d+)$")


def _extract_dynamic_configs(
    node_config: dict,
    inputs: List[Port],
    outputs: List[Port],
) -> Tuple[Dict[str, DynamicPortConfig], List[Port], List[Port]]:
    """Build DynamicPortConfig(s) from either old-style `dynamic_ports = "Prefix"`
    or new-style `[dynamic_ports.inputs]` tables.

    For old-style: ports named "{Prefix} {N}" are removed from the static
    inputs/outputs lists and a DynamicPortConfig is created.  New ports will be
    seeded from min_count at node-creation time.

    Returns (configs, filtered_inputs, filtered_outputs).
    """
    configs: Dict[str, DynamicPortConfig] = {}

    # --- new-style tables ---
    dyn_tables = node_config.get("dynamic_ports", None)
    if isinstance(dyn_tables, dict):
        for direction_key, cfg_data in dyn_tables.items():
            if not isinstance(cfg_data, dict):
                continue
            direction = "in" if direction_key == "inputs" else "out"
            group_name = cfg_data.get("group_name", f"{cfg_data.get('prefix', 'dyn')}_{direction_key}")
            cfg = DynamicPortConfig(
                group_name=group_name,
                prefix=cfg_data.get("prefix", "Port"),
                data_type=cfg_data.get("data_type", "any"),
                direction=direction,
                min_count=cfg_data.get("min_count", 1),
                max_count=cfg_data.get("max_count", 16),
                auto_expand=cfg_data.get("auto_expand", True),
                preview=cfg_data.get("preview", True),
            )
            configs[group_name] = cfg
        return configs, inputs, outputs

    # --- old-style string ---
    if isinstance(dyn_tables, str) and dyn_tables:
        prefix = dyn_tables
        # find matching ports in inputs and outputs
        for port_list, direction in [(inputs, "in"), (outputs, "out")]:
            matching: List[Tuple[int, Port]] = []
            for idx, port in enumerate(port_list):
                m = _DYNAMIC_PORT_RE.match(port.name)
                if m and m.group(1) == prefix:
                    matching.append((int(m.group(2)), port))
            if not matching:
                continue
            matching.sort(key=lambda x: x[0])
            data_type = matching[0][1].data_type
            max_count = max(idx for idx, _ in matching)
            group_name = f"{prefix}_{direction}"
            cfg = DynamicPortConfig(
                group_name=group_name,
                prefix=prefix,
                data_type=data_type,
                direction=direction,
                min_count=min(2, max_count),
                max_count=max_count,
                auto_expand=True,
                preview=matching[0][1].preview,
            )
            configs[group_name] = cfg
            # remove matching ports from the static list (they'll be seeded dynamically)
            match_ids = {p.id for _, p in matching}
            if direction == "in":
                inputs = [p for p in inputs if p.id not in match_ids]
            else:
                outputs = [p for p in outputs if p.id not in match_ids]
        return configs, inputs, outputs

    return {}, inputs, outputs


# ---------------------------------------------------------------------------
# impl.py loading
# ---------------------------------------------------------------------------
_loaded_modules: set = set()


def _load_compute_logic(impl_file: Path, definition_id: str) -> Optional[ComputeLogic]:
    """Dynamically load impl.py and instantiate its ComputeLogic subclass.

    Returns None (and prints a warning) if loading fails — the caller will
    use a StubCompute instead so the node still appears in the palette.
    """
    module_name = f"nodes_discovered.{definition_id.replace('.', '_')}"
    if module_name in _loaded_modules:
        # already loaded; return a fresh instance if class was found
        mod = sys.modules.get(module_name)
    else:
        try:
            spec = importlib.util.spec_from_file_location(module_name, impl_file)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            _loaded_modules.add(module_name)
            mod = module
        except Exception as e:
            # clean up partial module
            sys.modules.pop(module_name, None)
            print(f"  [stub] {definition_id}: impl.py load failed — {type(e).__name__}: {e}")
            return None

    # find ComputeLogic subclass
    for _name, obj in inspect.getmembers(mod):
        if inspect.isclass(obj) and issubclass(obj, ComputeLogic) and obj is not ComputeLogic:
            try:
                return obj()
            except Exception as e:
                print(f"  [stub] {definition_id}: instantiation failed — {e}")
                return None
    return None


# ---------------------------------------------------------------------------
# Main discovery
# ---------------------------------------------------------------------------
def _discover_in_path(base_path: Path) -> tuple[int, int, int]:
    """Walk base_path, parse every node.toml, register NodeDefinitions.

    Returns (loaded, stubbed, failed) counts for this path."""
    if not base_path.exists():
        return (0, 0, 0)
    print(f"Discovering nodes in: {base_path}")

    # 1. load categories
    for cat_toml in base_path.glob("**/category.toml"):
        try:
            with cat_toml.open("rb") as f:
                cfg = tomllib.load(f)
            rel = cat_toml.parent.relative_to(base_path)
            cat_id = ".".join(rel.parts)
            _ensure_category(
                cat_id,
                cfg.get("display_name", cat_id.split(".")[-1].capitalize()),
                cfg.get("order", 100),
                cfg.get("default_open", True),
            )
        except Exception as e:
            print(f"  [warn] category.toml parse failed: {cat_toml} — {e}")

    # 2. load nodes
    toml_files = sorted(base_path.glob("**/node.toml"))
    loaded = 0
    stubbed = 0
    failed = 0
    for toml_file in toml_files:
        try:
            with toml_file.open("rb") as f:
                node_config = tomllib.load(f)

            definition_id = node_config["name"]
            version = node_config.get("version", "1.0.0")
            display_name = node_config.get("display_name", definition_id)
            description = node_config.get("description", "")
            order = node_config.get("order", 100)

            # derive category id (everything before the last segment)
            parts = definition_id.rsplit(".", 1)
            category_id = parts[0] if len(parts) > 1 else "general"
            # ensure category exists in tree (even without category.toml)
            _ensure_category(category_id)

            inputs, outputs = _parse_ports(node_config)
            properties = _parse_properties(node_config)
            configs, inputs, outputs = _extract_dynamic_configs(node_config, inputs, outputs)

            # load impl.py
            impl_file = toml_file.parent / "impl.py"
            compute_logic = None
            stub_reason = None
            if impl_file.exists():
                compute_logic = _load_compute_logic(impl_file, definition_id)
                if compute_logic is None:
                    stub_reason = "dependencies unavailable"
            else:
                stub_reason = "impl.py not found"

            if compute_logic is None:
                compute_logic = StubCompute(definition_id, stub_reason or "unknown")
                stubbed += 1
            else:
                loaded += 1

            node_def = NodeDefinition(
                definition_id=definition_id,
                version=version,
                display_name=display_name,
                description=description,
                order=order,
                category=category_id,
                measure_time=node_config.get("measure_time", True),
                inputs=inputs,
                outputs=outputs,
                properties=properties,
                dynamic_port_configs=configs,
                compute_logic=compute_logic,
                is_source_node=node_config.get("is_source_node", None),
            )
            register_node(node_def)
        except Exception as e:
            print(f"  [error] failed to parse {toml_file}: {e}")
            failed += 1

    print(f"  → {loaded} with compute, {stubbed} stubbed, {failed} failed ({len(toml_files)} total)")
    return (loaded, stubbed, failed)


def discover_all_nodes(base_path: Path | None = None):
    """Walk all search paths, parse every node.toml, register NodeDefinitions.

    If `base_path` is given, only that path is searched (backward compat).
    Otherwise all paths in NODE_SEARCH_PATHS are searched in order; later
    registrations for the same definition_id+version overwrite earlier ones.
    """
    paths = [base_path] if base_path else NODE_SEARCH_PATHS
    total_loaded = total_stubbed = total_failed = 0
    for p in paths:
        if not p.exists():
            continue
        l, s, f = _discover_in_path(p)
        total_loaded += l
        total_stubbed += s
        total_failed += f
    print(f"Discovery complete: {total_loaded} nodes loaded with compute, "
          f"{total_stubbed} stubbed, {total_failed} failed entirely.")


# Auto-run on import
discover_all_nodes()
