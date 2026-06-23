"""
core.py - Refactored execution engine.

Implements:
  * Three-state execution model (frame-complete / idle / exhausted) without a
    single "all done" boolean.
  * Generator + step execution: `execute_step()` and `execute_generator()`.
  * Frame synchronisation with configurable timeout (default 5s).
  * Trigger modes ALL / ANY per node.
  * Cross-level data access: explicit connection first, else ComboBox source
    stored in node.properties["input_sources"].
  * True dynamic ports (create/delete at runtime, re-indexed, template names).
  * Topological-order filtering for ComboBox candidates (no cycles).
  * Frame cache with automatic cleanup of old frames.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from models import Connection, DynamicPortConfig, Node, Port, generate_id
from node_def import NodeDefinition, get_node_definition, _add_dynamic_port_to_lists


# ===========================================================================
# Execution state
# ===========================================================================
@dataclass
class NodeExecutionState:
    """Per-node execution state for the current frame."""
    executed: bool = False             # executed this frame
    skipped: bool = False              # skipped (timeout) this frame
    last_frame: int = -1               # frame id of last execution
    execute_count: int = 0             # how many times executed this frame (ANY)
    fresh_inputs: Set[str] = field(default_factory=set)  # port names with current-frame data
    wait_start: Optional[float] = None  # timestamp when started waiting


class GraphExecutionState:
    """Tracks everything needed to drive sharded execution."""

    def __init__(self, max_frames: int = 8, sync_timeout: float = 5.0):
        self.frame_id: int = 0
        # port_id -> (frame_id, value)  (latest value per port)
        self.port_data: Dict[str, Tuple[int, Any]] = {}
        self.node_states: Dict[str, NodeExecutionState] = {}
        self.source_depleted: Dict[str, bool] = {}
        self.max_frames = max_frames
        self.sync_timeout = sync_timeout
        self._frame_history: deque = deque(maxlen=max_frames)
        self.frame_started: bool = False  # True between start_frame and frame_complete

    # -- frame lifecycle ---------------------------------------------------
    def start_frame(self):
        """Begin a new frame: advance frame_id and reset per-node exec flags."""
        self.frame_id += 1
        self._frame_history.append(self.frame_id)
        self.node_states.clear()
        self.frame_started = True

    def reset(self):
        """Full reset (new run)."""
        self.frame_id = 0
        self.port_data.clear()
        self.node_states.clear()
        self.source_depleted.clear()
        self._frame_history.clear()
        self.frame_started = False

    # -- port data ---------------------------------------------------------
    def set_port_data(self, port_id: str, value: Any, frame_id: Optional[int] = None):
        fid = frame_id if frame_id is not None else self.frame_id
        self.port_data[port_id] = (fid, value)

    def get_port_data(self, port_id: str) -> Tuple[Optional[int], Any]:
        return self.port_data.get(port_id, (None, None))

    def ensure_node_state(self, node_id: str) -> NodeExecutionState:
        if node_id not in self.node_states:
            self.node_states[node_id] = NodeExecutionState()
        return self.node_states[node_id]


# ===========================================================================
# Execution result
# ===========================================================================
@dataclass
class ExecutionResult:
    """Returned by `execute_step`."""
    status: str  # "running" | "frame_complete" | "idle" | "exhausted"
    frame_id: int
    executed_nodes: List[str] = field(default_factory=list)
    skipped_nodes: List[str] = field(default_factory=list)
    waiting_nodes: List[str] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)        # "node_id.port_name" -> value
    errors: Dict[str, str] = field(default_factory=dict)
    node_times: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    elapsed_ms: float = 0.0


# ===========================================================================
# Graph
# ===========================================================================
class Graph:
    """The node graph + execution engine."""

    def __init__(self, sync_timeout: float = 5.0, max_frames: int = 8):
        self.id: str = generate_id("graph")
        self.nodes: List[Node] = []
        self.connections: List[Connection] = []
        self.graph_format_version: str = "1.0.0"
        # execution
        self.exec_state = GraphExecutionState(max_frames=max_frames, sync_timeout=sync_timeout)
        self.sync_timeout = sync_timeout
        # topology caches
        self._topo_order: List[str] = []          # node ids in topo order
        self._topo_index: Dict[str, int] = {}
        self._node_map: Dict[str, Node] = {}
        self._out_adj: Dict[str, List[str]] = {}
        self._in_adj: Dict[str, List[str]] = {}
        self._dirty = True

    # ------------------------------------------------------------------ #
    # Topology
    # ------------------------------------------------------------------ #
    def _mark_dirty(self):
        self._dirty = True

    def _rebuild_topology(self):
        self._node_map = {n.id: n for n in self.nodes}
        self._out_adj = {n.id: [] for n in self.nodes}
        self._in_adj = {n.id: [] for n in self.nodes}
        for c in self.connections:
            if c.from_node_id in self._out_adj:
                self._out_adj[c.from_node_id].append(c.to_node_id)
            if c.to_node_id in self._in_adj:
                self._in_adj[c.to_node_id].append(c.from_node_id)
        # Kahn's topological sort, stable by node insertion order
        order_index = {n.id: i for i, n in enumerate(self.nodes)}
        in_degree = {n.id: len(self._in_adj[n.id]) for n in self.nodes}
        # seed with in-degree 0, sorted by insertion order
        queue = sorted([nid for nid, d in in_degree.items() if d == 0], key=lambda x: order_index[x])
        topo: List[str] = []
        while queue:
            nid = queue.pop(0)
            topo.append(nid)
            ready = []
            for nxt in self._out_adj.get(nid, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    ready.append(nxt)
            ready.sort(key=lambda x: order_index[x])
            queue.extend(ready)
            # keep queue sorted by insertion order for stability
            queue.sort(key=lambda x: order_index[x])
        if len(topo) != len(self.nodes):
            raise RuntimeError("Graph has a cycle; cannot build topology.")
        self._topo_order = topo
        self._topo_index = {nid: i for i, nid in enumerate(topo)}
        self._dirty = False

    def _ensure_topology(self):
        if self._dirty:
            self._rebuild_topology()

    # ------------------------------------------------------------------ #
    # Graph mutation
    # ------------------------------------------------------------------ #
    def add_node(self, node: Node):
        self.nodes.append(node)
        self.exec_state.source_depleted.setdefault(node.id, False)
        self._mark_dirty()

    def remove_node(self, node_id: str) -> bool:
        before = len(self.nodes)
        self.nodes = [n for n in self.nodes if n.id != node_id]
        # remove related connections
        self.connections = [
            c for c in self.connections
            if c.from_node_id != node_id and c.to_node_id != node_id
        ]
        self.exec_state.source_depleted.pop(node_id, None)
        self._mark_dirty()
        return len(self.nodes) < before

    def add_connection(self, conn: Connection) -> Tuple[bool, Optional[str]]:
        """Add a connection.  Enforces single-input constraint and type compat.

        Returns (ok, error_message).
        """
        # validate ports exist
        from_node = self._find_node(conn.from_node_id)
        to_node = self._find_node(conn.to_node_id)
        if not from_node or not to_node:
            return False, "Source or target node not found."
        from_port = _find_port(from_node.outputs + from_node.inputs, conn.from_port_id)
        to_port = _find_port(to_node.inputs + to_node.outputs, conn.to_port_id)
        if not from_port or not to_port:
            return False, "Port not found."
        if to_port.direction != "in":
            return False, "Target port is not an input."
        # single-input constraint
        for existing in self.connections:
            if existing.to_port_id == conn.to_port_id:
                return False, "Input port already has a connection (single-input rule)."
        # type compatibility (int<->float allowed; 'any' always allowed)
        if not _types_compatible(from_port.data_type, to_port.data_type):
            return False, f"Type mismatch: {from_port.data_type} -> {to_port.data_type}."
        # no self loop / no cycle: ensure from_node is strictly upstream
        self._ensure_topology()
        if conn.from_node_id == conn.to_node_id:
            return False, "Self-connection not allowed."
        # adding this edge must not create a cycle: temporarily check
        if conn.to_node_id in self._ancestors(conn.from_node_id):
            return False, "Connection would create a cycle."
        self.connections.append(conn)
        self._mark_dirty()
        return True, None

    def remove_connection(self, conn_id: str) -> bool:
        before = len(self.connections)
        self.connections = [c for c in self.connections if c.id != conn_id]
        self._mark_dirty()
        return len(self.connections) < before

    def _ancestors(self, node_id: str) -> Set[str]:
        """Return all ancestors (transitive predecessors) of node_id."""
        self._ensure_topology()
        seen: Set[str] = set()
        stack = list(self._in_adj.get(node_id, []))
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            stack.extend(self._in_adj.get(nid, []))
        return seen

    # ------------------------------------------------------------------ #
    # Node property / config mutation
    # ------------------------------------------------------------------ #
    def set_node_properties(self, node_id: str, properties: Dict[str, Any]) -> bool:
        node = self._find_node(node_id)
        if not node:
            return False
        node.properties.update(properties)
        return True

    def set_trigger_mode(self, node_id: str, mode: str) -> bool:
        node = self._find_node(node_id)
        if not node or mode not in ("ALL", "ANY"):
            return False
        node.trigger_mode = mode
        return True

    def set_input_source(self, node_id: str, port_name: str, source: Optional[str]) -> bool:
        """Set/clear a ComboBox cross-level input source for a port."""
        node = self._find_node(node_id)
        if not node:
            return False
        node.set_input_source(port_name, source)
        return True

    # ------------------------------------------------------------------ #
    # Dynamic ports
    # ------------------------------------------------------------------ #
    def add_dynamic_port(self, node_id: str, group_name: str) -> Optional[Port]:
        node = self._find_node(node_id)
        if not node:
            return None
        cfg = node.dynamic_port_configs.get(group_name)
        if not cfg:
            return None
        # count existing dynamic ports in this group
        existing = [p for p in node.inputs + node.outputs
                    if p.metadata.get("dynamic_group") == group_name]
        if len(existing) >= cfg.max_count:
            return None
        next_index = len(existing)  # 0-based -> display +1
        port = _add_dynamic_port_to_lists(node.inputs, node.outputs, cfg, next_index)
        # gate nodes mirror Data inputs → Data outputs immediately
        self._sync_gate_outputs(node)
        self._mark_dirty()
        return port

    def remove_dynamic_port(self, node_id: str, port_id: str) -> Tuple[bool, Optional[str]]:
        node = self._find_node(node_id)
        if not node:
            return False, "Node not found."
        # find port
        port = _find_port(node.inputs + node.outputs, port_id)
        if not port:
            return False, "Port not found."
        if not port.metadata.get("is_dynamic"):
            return False, "Port is not dynamic."
        group = port.metadata.get("dynamic_group")
        cfg = node.dynamic_port_configs.get(group)
        if not cfg:
            return False, "Dynamic group config missing."
        # must be unconnected
        for c in self.connections:
            if c.to_port_id == port_id or c.from_port_id == port_id:
                return False, "Cannot delete a connected dynamic port."
        # must keep min_count
        existing = [p for p in node.inputs + node.outputs
                    if p.metadata.get("dynamic_group") == group]
        if len(existing) <= cfg.min_count:
            return False, f"At least {cfg.min_count} ports must remain in group '{group}'."
        # remove port
        node.inputs = [p for p in node.inputs if p.id != port_id]
        node.outputs = [p for p in node.outputs if p.id != port_id]
        # re-index remaining dynamic ports in the group (rename by template)
        remaining_in = [p for p in node.inputs if p.metadata.get("dynamic_group") == group]
        remaining_out = [p for p in node.outputs if p.metadata.get("dynamic_group") == group]
        for i, p in enumerate(remaining_in):
            new_name = f"{cfg.prefix} {i + 1}"
            p.name = new_name
            p.display_name = new_name
            p.metadata["dynamic_index"] = i + 1
        for i, p in enumerate(remaining_out):
            new_name = f"{cfg.prefix} {i + 1}"
            p.name = new_name
            p.display_name = new_name
            p.metadata["dynamic_index"] = i + 1
        self._mark_dirty()
        return True, None

    def rename_port(self, node_id: str, port_id: str, display_name: str) -> Tuple[bool, Optional[str]]:
        node = self._find_node(node_id)
        if not node:
            return False, "Node not found."
        port = _find_port(node.inputs + node.outputs, port_id)
        if not port:
            return False, "Port not found."
        port.display_name = display_name or None
        return True, None

    def maybe_auto_expand(self, node_id: str, port_id: str):
        """If the just-connected port is the last dynamic port in its group and
        auto_expand is on, create the next port."""
        node = self._find_node(node_id)
        if not node:
            return
        port = _find_port(node.inputs + node.outputs, port_id)
        if not port or not port.metadata.get("is_dynamic"):
            # still sync gate outputs in case a Data input was connected
            self._sync_gate_outputs(node)
            return
        group = port.metadata.get("dynamic_group")
        cfg = node.dynamic_port_configs.get(group)
        if not cfg or not cfg.auto_expand:
            self._sync_gate_outputs(node)
            return
        existing = [p for p in node.inputs + node.outputs
                    if p.metadata.get("dynamic_group") == group]
        # is this the last one (highest index)?
        max_index = max(p.metadata.get("dynamic_index", 0) for p in existing)
        if port.metadata.get("dynamic_index") == max_index:
            self.add_dynamic_port(node_id, group)
        # always sync gate outputs (add_dynamic_port already calls it, but
        # the early-return paths above need it too)
        self._sync_gate_outputs(node)

    def _sync_gate_outputs(self, node: Node):
        """For gate-type nodes (definition_id == logic.condition_gate), ensure
        a 'Data N' output exists for every 'Data N' input.  Called whenever a
        dynamic port is added or a connection is made so outputs appear
        immediately (not just after the first execution)."""
        if node.definition_id != "logic.condition_gate":
            return
        input_names = {p.name for p in node.inputs if p.name.startswith("Data ")}
        output_names = {p.name for p in node.outputs if p.name.startswith("Data ")}
        missing = input_names - output_names
        if not missing:
            return
        for name in missing:
            node.outputs.append(Port(
                name=name,
                display_name=name,
                data_type="any",
                direction="out",
                preview=False,
            ))
        self._mark_dirty()

    # ------------------------------------------------------------------ #
    # Source data & frame control
    # ------------------------------------------------------------------ #
    def start_frame(self):
        """Begin a new frame.  Resets per-node execution flags for this frame."""
        self.exec_state.start_frame()

    def set_source_data(self, node_id: str, data: Dict[str, Any]):
        """Push source output data for the *current* frame onto a source node.
        Does NOT advance the frame; call `start_frame` to begin a new frame."""
        node = self._find_node(node_id)
        if not node:
            return
        # auto-start frame 1 if never started (convenience)
        if self.exec_state.frame_id == 0:
            self.exec_state.start_frame()
        for out_port in node.outputs:
            if out_port.name in data:
                self.exec_state.set_port_data(out_port.id, data[out_port.name])

    def mark_source_depleted(self, node_id: str):
        self.exec_state.source_depleted[node_id] = True

    def reset_frame_state(self):
        self.exec_state.reset()

    # ------------------------------------------------------------------ #
    # ComboBox candidate enumeration (cross-level access)
    # ------------------------------------------------------------------ #
    def get_combobox_candidates(
        self, node_id: str, port_name: str
    ) -> List[Dict[str, Any]]:
        """Return output ports that are reachable from this node via actual
        connections (the upstream pipeline), filtered by type compatibility.

        Rule (per user spec):
          - Only nodes that are connected ancestors of `node_id` are listed.
            "Connected ancestor" = reachable by following connection edges
            backwards from this node's input ports.
          - This means: if node8's pipeline is node5→node6→node7→node8,
            node8 only sees outputs from node5/6/7, NOT from unrelated
            branches like node1→node2→node3→node4.
          - If the node has NO wired inputs (all inputs unconnected), the
            list is empty — the ComboBox stays empty until at least one
            wire is established.
        """
        self._ensure_topology()
        node = self._find_node(node_id)
        if not node:
            return []
        in_port = next((p for p in node.inputs if p.name == port_name), None)
        if not in_port:
            return []

        # Collect the set of node-ids that are connected ancestors of THIS
        # node (follow edges backwards from this node's wired input ports).
        connected_ancestors = self._connected_ancestors(node_id)

        candidates: List[Dict[str, Any]] = []
        for ancestor_id in connected_ancestors:
            ancestor = self._node_map.get(ancestor_id)
            if not ancestor:
                continue
            for op in ancestor.outputs:
                if _types_compatible(op.data_type, in_port.data_type):
                    candidates.append({
                        "node_id": ancestor.id,
                        "node_name": ancestor.name,
                        "port_id": op.id,
                        "port_name": op.name,
                        "display_name": op.display_name or op.name,
                        "data_type": op.data_type,
                        "label": f"{ancestor.name}.{op.display_name or op.name}",
                    })
        return candidates

    def _connected_ancestors(self, node_id: str) -> Set[str]:
        """Return the set of node ids reachable from `node_id` by following
        connection edges backwards (i.e. the actual wired upstream pipeline).

        Only nodes connected via real wires are included — topological-order
        proximity without a wire does NOT count.
        """
        self._ensure_topology()
        seen: Set[str] = set()
        # seed with the direct upstream nodes of this node's wired inputs
        stack: List[str] = []
        for c in self.connections:
            if c.to_node_id == node_id and c.from_node_id not in seen:
                seen.add(c.from_node_id)
                stack.append(c.from_node_id)
        # walk backwards
        while stack:
            nid = stack.pop()
            for c in self.connections:
                if c.to_node_id == nid and c.from_node_id not in seen:
                    seen.add(c.from_node_id)
                    stack.append(c.from_node_id)
        return seen

    # ------------------------------------------------------------------ #
    # Input collection (connection first, then ComboBox)
    # ------------------------------------------------------------------ #
    def _collect_inputs(self, node: Node) -> Tuple[Dict[str, Any], bool, List[str]]:
        """Collect inputs for a node.

        Returns (inputs_dict, all_connected_ready, missing_port_names).
        - `all_connected_ready` is True when every *connected* input port has
          data from the current frame (frame-sync condition for ALL mode).
        - `missing_port_names` lists connected input ports without current-frame
          data.
        """
        inputs: Dict[str, Any] = {}
        missing: List[str] = []
        current_frame = self.exec_state.frame_id
        all_ready = True

        for in_port in node.inputs:
            value = None
            resolved = False
            connected = False
            # 1. direct connection first
            for c in self.connections:
                if c.to_port_id == in_port.id:
                    connected = True
                    fid, v = self.exec_state.get_port_data(c.from_port_id)
                    if fid == current_frame and v is not None:
                        value = _coerce(v, in_port.data_type)
                        # decode base64→numpy for image inputs (original nodes expect numpy)
                        if in_port.data_type == "image":
                            value = _decode_image_input(value)
                        resolved = True
                    else:
                        missing.append(in_port.name)
                        all_ready = False
                    break
            # 2. ComboBox source (only if not connected)
            if not connected:
                src = node.get_input_source(in_port.name)
                if src:
                    src_node_id, src_port_name = src.split(".", 1)
                    src_node = self._find_node(src_node_id)
                    if src_node:
                        src_port = next((p for p in src_node.outputs if p.name == src_port_name), None)
                        if src_port:
                            fid, v = self.exec_state.get_port_data(src_port.id)
                            if v is not None:
                                value = _coerce(v, in_port.data_type)
                                if in_port.data_type == "image":
                                    value = _decode_image_input(value)
                                resolved = True
            # 3. fall back to property default
            if not resolved and in_port.name in node.properties:
                value = _coerce(node.properties[in_port.name], in_port.data_type)
                resolved = True

            inputs[in_port.name] = value
        return inputs, all_ready, missing

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def execute_step(self, context: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        """Execute one step: process every currently-ready node in topo order.

        Returns an ExecutionResult whose `status` is one of:
          - "running"       : some nodes executed; more may become ready.
          - "frame_complete": all nodes done/skipped this frame, none waiting.
          - "idle"          : nothing ready, nothing waiting, sources not depleted.
          - "exhausted"     : nothing ready, nothing waiting, all sources depleted.
        """
        start = time.perf_counter()
        ctx = context or {}
        self._ensure_topology()
        st = self.exec_state
        result = ExecutionResult(status="idle", frame_id=st.frame_id)

        if st.frame_id == 0 or not st.frame_started:
            # no frame has been started -> nothing to do
            result.status = "exhausted" if self._all_sources_depleted() else "idle"
            result.elapsed_ms = (time.perf_counter() - start) * 1000
            return result

        any_executed = False

        for nid in self._topo_order:
            node = self._node_map[nid]
            ns = st.ensure_node_state(nid)

            # Already executed this frame under ALL -> skip
            if node.trigger_mode == "ALL" and ns.executed:
                continue
            if ns.skipped:
                continue

            is_source = node.effective_is_source
            # Has the source already received pushed data this frame?
            source_has_pushed = (
                is_source
                and any(st.get_port_data(op.id)[0] == st.frame_id for op in node.outputs)
            )

            # Collect inputs (sources with pushed data skip input collection)
            if is_source and not node.inputs:
                inputs: Dict[str, Any] = {}
                all_ready = True
                missing: List[str] = []
            else:
                inputs, all_ready, missing = self._collect_inputs(node)

            # Determine readiness
            connected_inputs = [
                p for p in node.inputs
                if any(c.to_port_id == p.id for c in self.connections)
            ]
            has_connected = len(connected_inputs) > 0

            ready = False
            if is_source and not has_connected:
                # source node: ready if pushed data exists, or always (compute from props)
                ready = True
            else:
                if node.trigger_mode == "ALL":
                    ready = all_ready and (has_connected or not node.inputs)
                    if not has_connected and node.inputs:
                        ready = all_ready
                else:  # ANY
                    ready = any(
                        st.get_port_data(c.from_port_id)[0] == st.frame_id
                        for c in self.connections if c.to_port_id in {p.id for p in node.inputs}
                    )
                    if not has_connected and node.inputs:
                        ready = all_ready

            if not ready:
                if ns.wait_start is None:
                    ns.wait_start = time.perf_counter()
                waited = time.perf_counter() - ns.wait_start
                if waited >= st.sync_timeout:
                    ns.skipped = True
                    result.skipped_nodes.append(nid)
                    result.errors[nid] = f"Frame-sync timeout after {st.sync_timeout:.1f}s"
                else:
                    result.waiting_nodes.append(nid)
                continue

            # ---- execute ----
            node_start = time.perf_counter()
            try:
                # If a source already has pushed data for this frame, do NOT
                # re-run compute (the pushed data wins).  Just expose it.
                if source_has_pushed:
                    for op in node.outputs:
                        fid, val = st.get_port_data(op.id)
                        if fid == st.frame_id:
                            result.outputs[f"{node.id}.{op.name}"] = _serialize_output(val, op.data_type)
                else:
                    definition = get_node_definition(node.definition_id, node.definition_version)
                    node_ctx = {**ctx, "node_id": node.id, "_graph": self}
                    outputs = definition.compute(inputs, dict(node.properties), node_ctx)
                    # after compute, the node may have added output ports
                    # dynamically (e.g. Condition Gate mirrors Data inputs);
                    # collect any new outputs the node now has.
                    for op in node.outputs:
                        if op.name in outputs:
                            val = outputs[op.name]
                            # store raw value internally (numpy stays numpy)
                            st.set_port_data(op.id, val)
                            # serialize for API response (numpy→base64 for images)
                            result.outputs[f"{node.id}.{op.name}"] = _serialize_output(val, op.data_type)
                    for key in ("__display_text__", "__frame_count__", "__error__"):
                        if key in outputs:
                            result.outputs[f"{node.id}{key}"] = outputs[key]
                    if "__error__" in outputs:
                        result.errors[node.id] = str(outputs["__error__"])
                ns.executed = True
                ns.execute_count += 1
                ns.last_frame = st.frame_id
                ns.wait_start = None
                result.executed_nodes.append(nid)
                any_executed = True
            except Exception as e:  # noqa: BLE001
                ns.executed = True
                ns.execute_count += 1
                result.errors[node.id] = str(e)
                result.executed_nodes.append(nid)
                any_executed = True

            # timing
            definition_measure = True
            try:
                definition = get_node_definition(node.definition_id, node.definition_version)
                definition_measure = definition.measure_time
            except Exception:
                pass
            if definition_measure:
                result.node_times[node.id] = {
                    "name": node.name,
                    "time": (time.perf_counter() - node_start) * 1000,
                    "order": len(result.node_times),
                }

        # ---- decide status ----
        if any_executed:
            # if there are still waiting nodes, we're mid-frame
            if result.waiting_nodes:
                result.status = "running"
            else:
                result.status = "frame_complete"
                st.frame_started = False  # frame finished; need start_frame for next
        else:
            if result.waiting_nodes:
                result.status = "running"  # waiting but nothing executed this pass
            elif self._all_sources_depleted():
                result.status = "exhausted"
            else:
                result.status = "idle"

        result.elapsed_ms = (time.perf_counter() - start) * 1000
        return result

    def execute_generator(self, context: Optional[Dict[str, Any]] = None, max_steps: int = 10000):
        """Generator yielding ExecutionResults until idle/exhausted."""
        steps = 0
        while steps < max_steps:
            res = self.execute_step(context)
            yield res
            if res.status in ("idle", "exhausted", "frame_complete"):
                # frame_complete: stop generator (caller resets for next frame)
                if res.status == "frame_complete":
                    return
                if res.status in ("idle", "exhausted"):
                    return
            steps += 1

    def _all_sources_depleted(self) -> bool:
        sources = [n for n in self.nodes if n.effective_is_source]
        if not sources:
            return False
        return all(self.exec_state.source_depleted.get(n.id, False) for n in sources)

    # ------------------------------------------------------------------ #
    # Auto-layout (layered / Sugiyama-style)
    # ------------------------------------------------------------------ #
    def compute_auto_layout(
        self,
        direction: str = "LR",
        node_width: int = 220,
        node_height: int = 120,
        layer_gap: int = 80,
        node_gap: int = 40,
    ) -> Dict[str, Dict[str, float]]:
        """Compute new positions for all nodes using a layered layout.

        Implements a simplified Sugiyama-style algorithm:
          1. Cycle-aware topological layering (longest-path from any source).
             Self-loops and back-edges are ignored for layout purposes.
          2. Brandes-Kösch median-based crossing reduction (a few iterations).
          3. Coordinate assignment: layers along the primary axis, nodes within
             a layer stacked along the secondary axis, centered around 0.

        Returns ``{node_id: {"x": float, "y": float}}``.  The caller is
        responsible for persisting these positions on the Node instances.
        """
        self._ensure_topology()
        if not self.nodes:
            return {}

        node_ids = [n.id for n in self.nodes]
        id_set = set(node_ids)

        # Build forward adjacency list (only edges that don't form cycles
        # for layout purposes).  We skip self-loops and edges that go from
        # a higher topo-order node to a lower one (back-edges).
        topo_idx = self._topo_index
        forward: Dict[str, List[str]] = {nid: [] for nid in node_ids}
        in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}
        for c in self.connections:
            if c.from_node_id == c.to_node_id:
                continue  # self-loop
            if c.from_node_id not in id_set or c.to_node_id not in id_set:
                continue
            # ignore back-edges (would create cycle in DAG layout)
            if topo_idx.get(c.from_node_id, 0) > topo_idx.get(c.to_node_id, 0):
                continue
            forward[c.from_node_id].append(c.to_node_id)
            in_degree[c.to_node_id] += 1

        # 1. Layer assignment via longest path from any source.
        layers: Dict[str, int] = {}

        def longest_path(nid: str, visiting: Set[str]) -> int:
            if nid in layers:
                return layers[nid]
            if nid in visiting:
                return 0  # safety against any residual cycle
            visiting.add(nid)
            children = forward.get(nid, [])
            if not children:
                layer = 0
            else:
                layer = 1 + max(longest_path(c, visiting) for c in children)
            layers[nid] = layer
            visiting.discard(nid)
            return layer

        for nid in node_ids:
            if in_degree[nid] == 0:
                longest_path(nid, set())
        # Any nodes not reached (e.g. inside cycles) get placed at layer 0.
        for nid in node_ids:
            if nid not in layers:
                longest_path(nid, set())

        # Group nodes by layer
        max_layer = max(layers.values()) if layers else 0
        nodes_in_layer: List[List[str]] = [[] for _ in range(max_layer + 1)]
        for nid, layer in layers.items():
            nodes_in_layer[layer].append(nid)

        # 2. Crossing reduction (Brandes-Kösch median heuristic).
        # Initialise order within each layer by node insertion order.
        order_in_layer: List[List[str]] = [list(lst) for lst in nodes_in_layer]
        for _ in range(8):  # a few sweeps
            # downward sweep
            for li in range(1, len(order_in_layer)):
                current = order_in_layer[li]
                # compute median of upstream positions
                pos_in_prev: Dict[str, float] = {nid: i for i, nid in enumerate(order_in_layer[li - 1])}
                def median(nid: str) -> float:
                    ups = [c for c in self.connections if c.to_node_id == nid and c.from_node_id in pos_in_prev]
                    if not ups:
                        return float("inf")
                    poses = sorted(pos_in_prev[c.from_node_id] for c in ups)
                    n = len(poses)
                    return poses[n // 2] if n % 2 == 1 else (poses[n // 2 - 1] + poses[n // 2]) / 2.0
                current.sort(key=lambda nid: (median(nid), nid))
                order_in_layer[li] = current
            # upward sweep
            for li in range(len(order_in_layer) - 2, -1, -1):
                current = order_in_layer[li]
                pos_in_next: Dict[str, float] = {nid: i for i, nid in enumerate(order_in_layer[li + 1])}
                def median_up(nid: str) -> float:
                    downs = [c for c in self.connections if c.from_node_id == nid and c.to_node_id in pos_in_next]
                    if not downs:
                        return float("inf")
                    poses = sorted(pos_in_next[c.to_node_id] for c in downs)
                    n = len(poses)
                    return poses[n // 2] if n % 2 == 1 else (poses[n // 2 - 1] + poses[n // 2]) / 2.0
                current.sort(key=lambda nid: (median_up(nid), nid))
                order_in_layer[li] = current

        # 3. Coordinate assignment.
        # Primary axis = layer index, secondary axis = position within layer.
        positions: Dict[str, Dict[str, float]] = {}
        for li, layer_nodes in enumerate(order_in_layer):
            total_h = len(layer_nodes) * node_height + max(0, len(layer_nodes) - 1) * node_gap
            start_y = -total_h / 2.0
            for i, nid in enumerate(layer_nodes):
                if direction == "LR":
                    x = li * (node_width + layer_gap)
                    y = start_y + i * (node_height + node_gap)
                else:  # TB
                    y = li * (node_height + layer_gap)
                    x = start_y + i * (node_width + node_gap)
                positions[nid] = {"x": float(x), "y": float(y)}
        return positions

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _find_node(self, node_id: str) -> Optional[Node]:
        self._ensure_topology()
        return self._node_map.get(node_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "graph_format_version": self.graph_format_version,
            "nodes": [n.model_dump(mode="json") for n in self.nodes],
            "connections": [c.model_dump(mode="json") for c in self.connections],
        }


# ===========================================================================
# Module-level helpers
# ===========================================================================
def _find_port(ports: List[Port], port_id: str) -> Optional[Port]:
    for p in ports:
        if p.id == port_id:
            return p
    return None


def _types_compatible(src_type: Any, dst_type: Any) -> bool:
    if src_type == "any" or dst_type == "any":
        return True
    if src_type == dst_type:
        return True
    # int <-> float auto conversion
    if {src_type, dst_type} <= {"int", "float"}:
        return True
    return False


def _coerce(value: Any, data_type: Any) -> Any:
    if value is None:
        return None
    try:
        if data_type == "int":
            return int(value)
        if data_type == "float":
            return float(value)
    except (TypeError, ValueError):
        return value
    return value


def _serialize_output(value: Any, data_type: Any) -> Any:
    """Convert internal values to JSON-serialisable form for the API response.

    numpy arrays / PIL images → base64 data URI (for image-type ports).
    Everything else is returned unchanged.
    """
    if value is None:
        return None
    if data_type != "image":
        return value
    try:
        import numpy as np
        if isinstance(value, np.ndarray):
            return _numpy_to_base64(value)
    except ImportError:
        pass
    try:
        from PIL import Image
        if isinstance(value, Image.Image):
            import io, base64
            img = value
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception:
        pass
    # already a string (base64 or data URI) — return as-is
    if isinstance(value, str):
        return value
    return value


def _decode_image_input(value: Any) -> Any:
    """Decode a base64 data URI / raw base64 string to a numpy array in **BGR**
    channel order (the convention used by OpenCV / cv2-based nodes).

    numpy arrays are returned unchanged (assumed already BGR).  PIL images
    are converted BGR.  Used when feeding image inputs to original nodes
    that expect numpy arrays in OpenCV's BGR order.
    """
    if value is None:
        return None
    try:
        import numpy as np
        if isinstance(value, np.ndarray):
            return value
    except ImportError:
        pass
    if not isinstance(value, str):
        return value
    try:
        import base64
        import numpy as np
        try:
            import cv2
            # cv2.imdecode returns BGR — matches what nodes expect
            raw = value.split(",", 1)[1] if value.startswith("data:") else value
            buf = np.frombuffer(base64.b64decode(raw), np.uint8)
            arr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            return arr
        except ImportError:
            # fall back to PIL (RGB) then convert to BGR
            import io
            from PIL import Image
            raw = value.split(",", 1)[1] if value.startswith("data:") else value
            arr = np.array(Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB"))
            return arr[:, :, ::-1]  # RGB → BGR
    except Exception:
        return value


def _numpy_to_base64(arr) -> str:
    """Convert a numpy array to a base64 JPEG data URI.

    Nodes (webcam, image loaders, cv2-based nodes) return arrays in OpenCV's
    **BGR** channel order.  We use cv2.imencode (which expects BGR) so the
    colours come out correct — using PIL's Image.fromarray would interpret
    the array as RGB and swap red/blue.

    Uses JPEG (quality 85) for ~10x smaller payloads on photographic content
    — critical for streaming webcam footage at usable framerates.
    """
    import base64
    import numpy as np
    try:
        import cv2
        # downscale very large frames to cap payload size
        max_dim = 960
        h, w = arr.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            arr = cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
        # cv2.imencode expects BGR and produces a correct JPEG
        ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return ""
        return f"data:image/jpeg;base64,{base64.b64encode(buf.tobytes()).decode('ascii')}"
    except ImportError:
        # cv2 not available — fall back to PIL with explicit BGR→RGB conversion
        import io
        from PIL import Image
        # if 3-channel, assume BGR and convert to RGB
        if arr.ndim == 3 and arr.shape[2] == 3:
            arr = arr[:, :, ::-1]  # BGR → RGB
        img = Image.fromarray(arr)
        if max(img.size) > 960:
            ratio = 960 / max(img.size)
            img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
