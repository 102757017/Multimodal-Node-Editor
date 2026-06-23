"""Python client for the Multimodal Node Editor backend.

Three transport modes:
  - DirectClient: in-process, zero-overhead (numpy arrays pass by reference)
  - SharedMemoryClient: cross-process, zero-copy (via shared memory)
  - HttpClient: cross-machine, HTTP-based (base64-encoded images)

See ``multimodal_client.py`` for the full implementation.
"""
from .multimodal_client import (  # noqa: F401
    DirectClient,
    SharedMemoryClient,
    HttpClient,
    MultimodalClient,
    GraphInfo,
    NodeInfo,
    PortInfo,
    RunResult,
    TaskStatus,
    MultimodalError,
)

__all__ = [
    "DirectClient",
    "SharedMemoryClient",
    "HttpClient",
    "MultimodalClient",
    "GraphInfo",
    "NodeInfo",
    "PortInfo",
    "RunResult",
    "TaskStatus",
    "MultimodalError",
]
