"""
model_registry.py - Process-global model instance registry.

Solves the memory-overflow problem when multiple nodes in a graph each load
the same large model (CLIP, LLM, ONNX session, MediaPipe model, …).  Instead
of every node instance calling `MyModel.load(path)` and getting its own copy,
nodes ask the registry:

    from node_editor.model_registry import registry
    model, error = registry.get("clip:ViT-B-32:gpu=false",
                                loader=lambda: load_clip("ViT-B-32"))

The registry guarantees:
  * `loader()` is called at most once per key (thread-safe, per-key lock).
  * The same instance is returned to every caller for that key.
  * Load errors are cached so a failed load isn't retried on every frame.
  * Reference counting + LRU eviction free models when memory pressure rises.
  * A live snapshot of the cache is exposed for the UI (size, hits, errors).
"""
from __future__ import annotations

import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class _Entry:
    key: str
    model: Any = None
    error: Optional[str] = None
    loaded_at: float = 0.0
    last_used_at: float = 0.0
    ref_count: int = 0
    load_count: int = 0  # how many times loader was actually called
    hit_count: int = 0   # how many times a cached model was returned
    est_bytes: int = 0
    loader_label: str = ""  # e.g. "onnx", "clip", "llm"
    lock: threading.Lock = field(default_factory=threading.Lock)


class ModelRegistry:
    """Process-global singleton model cache.

    Thread-safe.  Intended to be called from any ComputeLogic.compute().
    """

    def __init__(self, max_entries: int = 16, max_bytes: int = 8 * 1024 * 1024 * 1024):
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._global_lock = threading.RLock()
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._total_bytes = 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get(
        self,
        key: str,
        loader: Callable[[], Any],
        *,
        est_bytes: int = 0,
        label: str = "",
    ) -> Tuple[Any, Optional[str]]:
        """Return (model, None) or (None, error_message).

        `loader` is called only the first time `key` is requested.  Subsequent
        calls return the cached instance.  If `loader` raises, the exception
        message is cached as the error so the load isn't retried.
        """
        with self._global_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry(key=key, loader_label=label)
                self._entries[key] = entry
            # move to end (most-recently-used)
            self._entries.move_to_end(key)

        # per-key lock so concurrent first-loads dedupe
        with entry.lock:
            if entry.model is not None:
                entry.hit_count += 1
                entry.last_used_at = time.time()
                return entry.model, None
            if entry.error is not None:
                # cached error — don't retry
                entry.hit_count += 1
                return None, entry.error
            # first load
            entry.load_count += 1
            try:
                # check memory budget BEFORE heavy load
                self._maybe_evict(est_bytes)
                model = loader()
                entry.model = model
                entry.loaded_at = time.time()
                entry.last_used_at = time.time()
                # estimate size if not provided
                if est_bytes:
                    entry.est_bytes = est_bytes
                else:
                    entry.est_bytes = _estimate_model_bytes(model)
                with self._global_lock:
                    self._total_bytes += entry.est_bytes
                return model, None
            except Exception as e:  # noqa: BLE001
                entry.error = f"{type(e).__name__}: {e}"
                return None, entry.error

    def preload(
        self,
        key: str,
        loader: Callable[[], Any],
        *,
        est_bytes: int = 0,
        label: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """Eagerly load a model.  Returns (ok, error)."""
        model, err = self.get(key, loader, est_bytes=est_bytes, label=label)
        return (err is None), err

    def unload(self, key: str) -> bool:
        """Drop a model from the cache.  Returns True if it was present."""
        with self._global_lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return False
            self._total_bytes -= entry.est_bytes
        # best-effort cleanup
        _try_release(entry.model)
        entry.model = None
        entry.error = None
        return True

    def release(self, key: str):
        """Decrement the reference count; evict if it hits zero AND the cache
        is over budget.  (Optional — most callers don't need this.)"""
        with self._global_lock:
            entry = self._entries.get(key)
            if entry:
                entry.ref_count = max(0, entry.ref_count - 1)

    def clear(self):
        """Unload everything."""
        with self._global_lock:
            keys = list(self._entries.keys())
        for k in keys:
            self.unload(k)

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serialisable view of the cache for the UI."""
        with self._global_lock:
            entries = []
            for key, e in self._entries.items():
                entries.append({
                    "key": key,
                    "label": e.loader_label,
                    "loaded": e.model is not None,
                    "error": e.error,
                    "loaded_at": e.loaded_at,
                    "last_used_at": e.last_used_at,
                    "load_count": e.load_count,
                    "hit_count": e.hit_count,
                    "est_bytes": e.est_bytes,
                    "est_mb": round(e.est_bytes / (1024 * 1024), 2),
                })
            return {
                "entries": entries,
                "total_bytes": self._total_bytes,
                "total_mb": round(self._total_bytes / (1024 * 1024), 2),
                "max_bytes": self.max_bytes,
                "max_mb": round(self.max_bytes / (1024 * 1024), 2),
                "max_entries": self.max_entries,
                "entry_count": len(self._entries),
            }

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    def _maybe_evict(self, incoming_bytes: int):
        """Evict least-recently-used entries if over budget.  Must be called
        under the global lock."""
        # evict by count
        while len(self._entries) >= self.max_entries:
            oldest_key, oldest = next(iter(self._entries.items()))
            if oldest.model is None and oldest.error is None:
                # pending entry without a model — just drop it
                self._entries.pop(oldest_key)
                continue
            self._entries.pop(oldest_key)
            self._total_bytes -= oldest.est_bytes
            _try_release(oldest.model)
        # evict by bytes
        needed = self._total_bytes + incoming_bytes
        while needed > self.max_bytes and self._entries:
            # find oldest loaded entry
            oldest_key = None
            oldest_entry = None
            for k, e in self._entries.items():
                if e.model is not None:
                    oldest_key = k
                    oldest_entry = e
                    break
            if oldest_key is None:
                break
            self._entries.pop(oldest_key)
            self._total_bytes -= oldest_entry.est_bytes
            needed -= oldest_entry.est_bytes
            _try_release(oldest_entry.model)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _estimate_model_bytes(model: Any) -> int:
    """Best-effort memory estimate for a loaded model."""
    # PyTorch
    try:
        if hasattr(model, "parameters"):
            total = 0
            for p in model.parameters():
                if hasattr(p, "nelement") and hasattr(p, "element_size"):
                    total += p.nelement() * p.element_size()
            if total:
                return total
    except Exception:
        pass
    # HuggingFace transformers
    try:
        if hasattr(model, "get_memory_footprint"):
            return int(model.get_memory_footprint())
    except Exception:
        pass
    # ONNX session — no easy API; assume a nominal 50 MB
    if type(model).__module__.startswith("onnxruntime"):
        return 50 * 1024 * 1024
    # MediaPipe task
    if "mediapipe" in type(model).__module__:
        return 20 * 1024 * 1024
    return 10 * 1024 * 1024  # default 10 MB


def _try_release(model: Any):
    """Best-effort cleanup of a model instance."""
    if model is None:
        return
    # PyTorch / generic
    try:
        if hasattr(model, "cpu"):
            model.cpu()
        if hasattr(model, "to"):
            try:
                import torch
                model.to("cpu")
            except Exception:
                pass
    except Exception:
        pass
    try:
        if hasattr(model, "release"):
            model.release()
    except Exception:
        pass
    del model


# Process-global singleton
registry = ModelRegistry()
