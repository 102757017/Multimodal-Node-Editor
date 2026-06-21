"""
Condition Gate node — filters data flow based on conditions.

Inputs:
  - Condition 1, 2, 3 (any): condition signals.  Each has a configurable
    comparison operator + threshold (set in the property panel).  In AND
    mode ALL must pass; in OR mode ANY must pass.  `invert` flips the result.
    Unconnected conditions are ignored (don't block the gate).
  - Data N (any, dynamic): data inputs.  Each connected Data input is echoed
    to a same-named output port ONLY when the gate is open this frame.

Outputs:
  - passed (bool): whether the gate is open this frame.
  - Data N (any): mirrors the data inputs; echoes the value when the gate is
    open, None when closed.  Output ports are auto-created to match inputs.

Usage: connect comparison results to the Condition inputs (or connect raw
values and set the comparison operator in the property panel), and connect
the data you want to gate to the Data inputs.  Downstream nodes receive the
data only when conditions are met — letting you route different conditions
to different branches.
"""
from typing import Any, Dict
from node_editor.node_def import ComputeLogic


def _truthy(v: Any) -> bool:
    """Python truthiness with explicit handling of numeric strings."""
    if isinstance(v, str):
        return v.lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _compare(value: Any, op: str, threshold: Any) -> bool:
    """Compare value against threshold using the given operator.

    Operators: ==, !=, >, >=, <, <=, truthy, contains (string).
    Falls back to truthiness if types are incompatible.
    """
    if op == "truthy":
        return _truthy(value)
    try:
        # numeric comparison
        v = float(value)
        t = float(threshold)
        if op == "==": return v == t
        if op == "!=": return v != t
        if op == ">": return v > t
        if op == ">=": return v >= t
        if op == "<": return v < t
        if op == "<=": return v <= t
    except (TypeError, ValueError):
        # string comparison
        vs = str(value)
        ts = str(threshold)
        if op == "==": return vs == ts
        if op == "!=": return vs != ts
        if op == ">": return vs > ts
        if op == ">=": return vs >= ts
        if op == "<": return vs < ts
        if op == "<=": return vs <= ts
        if op == "contains": return ts in vs
    return False


class ConditionGateLogic(ComputeLogic):
    def compute(
        self,
        inputs: Dict[str, Any],
        properties: Dict[str, Any],
        context: Any = None,
    ) -> Dict[str, Any]:
        mode = properties.get("mode", "AND")
        invert = bool(properties.get("invert", False))

        # --- ensure output ports mirror data inputs ---
        # The gate auto-creates a "Data N" output for every "Data N" input so
        # the user can wire each data input to a separate downstream branch.
        if context and isinstance(context, dict):
            self._sync_data_outputs(context.get("_graph"), context.get("node_id"))

        # --- evaluate conditions ---
        cond_results = []
        for i in (1, 2, 3):
            v = inputs.get(f"Condition {i}")
            if v is None:
                continue  # unconnected condition: ignored (doesn't block)
            op = properties.get(f"cond{i}_op", "truthy")
            threshold = properties.get(f"cond{i}_val", "")
            cond_results.append(_compare(v, op, threshold))

        if not cond_results:
            gate_open = True  # no conditions connected → always open
        elif mode == "OR":
            gate_open = any(cond_results)
        else:  # AND
            gate_open = all(cond_results)
        if invert:
            gate_open = not gate_open

        out: Dict[str, Any] = {"passed": gate_open}
        for key, val in inputs.items():
            if key.startswith("Data "):
                out[key] = val if gate_open else None
        return out

    def _sync_data_outputs(self, graph: Any, node_id: str):
        """Ensure the node has a 'Data N' output for every 'Data N' input."""
        if not graph or not node_id:
            return
        node = graph._find_node(node_id)
        if not node:
            return
        input_names = {p.name for p in node.inputs if p.name.startswith("Data ")}
        output_names = {p.name for p in node.outputs if p.name.startswith("Data ")}
        missing = input_names - output_names
        if not missing:
            return
        from models import Port
        for name in missing:
            node.outputs.append(Port(
                name=name,
                display_name=name,
                data_type="any",
                direction="out",
                preview=False,
            ))
        graph._mark_dirty()
