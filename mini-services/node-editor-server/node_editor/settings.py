"""node_editor.settings shim — provides get_setting for upstream nodes."""
import os
import json
from pathlib import Path


def _load_config() -> dict:
    cfg_path = os.environ.get("NODE_EDITOR_CONFIG")
    if not cfg_path:
        # look for config.json in the original project root
        for p in [
            Path("/home/z/my-project/download/multimodal-node-editor/config.json"),
            Path("config.json"),
        ]:
            if p.exists():
                cfg_path = str(p)
                break
    if not cfg_path or not Path(cfg_path).exists():
        return {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_CONFIG_CACHE: dict | None = None


def get_setting(key: str, default=None):
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = _load_config()
    # support dotted keys like "api_keys.openai"
    parts = key.split(".")
    val = _CONFIG_CACHE
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return default
    return val if val is not None else default
