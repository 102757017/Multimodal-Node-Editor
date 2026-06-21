"""node_editor package shim.

Re-exports the refactored models/logic under the original package path so
that the upstream nodes' `impl.py` files (`from node_editor.node_def import
ComputeLogic`, `from node_editor.image_utils import ...`, etc.) import cleanly.
"""
from .node_def import ComputeLogic, NodeDefinition  # noqa: F401
