"""
memory.config — configuration for all memory tiers.

Reads config/memory.toml at import time and exposes typed constants.
Nothing else reads the toml directly — this is the single source of truth
for memory configuration, same pattern as config/settings.py for LLM config.

Falls back to hardcoded defaults only when the toml file is absent.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "memory.toml"

_DEFAULTS: dict = {
    "working": {
        "storage_url": "redis://localhost:6379/0",
        "ttl_seconds": 0,
    },
    "episodic": {
        "storage_path": "./chroma_data",
        "collection_name": "episodic_memory",
    },
}


def _load() -> dict:
    """Load memory.toml; return defaults if the file is missing."""
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load()

_working = _cfg.get("working", _DEFAULTS["working"])
WORKING_STORAGE_URL: str = str(
    _working.get("storage_url", _DEFAULTS["working"]["storage_url"])
)
WORKING_TTL_SECONDS: int = int(
    _working.get("ttl_seconds", _DEFAULTS["working"]["ttl_seconds"])
)

_episodic = _cfg.get("episodic", _DEFAULTS["episodic"])
EPISODIC_STORAGE_PATH: str = str(
    _episodic.get("storage_path", _DEFAULTS["episodic"]["storage_path"])
)
EPISODIC_COLLECTION_NAME: str = str(
    _episodic.get("collection_name", _DEFAULTS["episodic"]["collection_name"])
)
