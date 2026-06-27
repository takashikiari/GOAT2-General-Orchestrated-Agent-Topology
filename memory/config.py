"""memory.config — memory tier config (working/episodic/permanent/session_cache/retrieval_budget). Reads config/memory.toml."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

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
    "permanent": {
        "letta_url": "http://localhost:8283",
        "agent_name": "goat-permanent",
        "letta_model": "letta/letta-free",
    },
    "session_cache": {
        "ttl_seconds": 300,
    },
    "retrieval_budget": {
        "max_results_per_search": 15,
        "max_context_tokens": 4000,
    },
}


def _load() -> dict:
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

_permanent = _cfg.get("permanent", _DEFAULTS["permanent"])
PERMANENT_LETTA_URL: str = str(
    _permanent.get("letta_url", _DEFAULTS["permanent"]["letta_url"])
)
PERMANENT_AGENT_NAME: str = str(
    _permanent.get("agent_name", _DEFAULTS["permanent"]["agent_name"])
)
PERMANENT_LETTA_MODEL: str = str(
    _permanent.get("letta_model", _DEFAULTS["permanent"]["letta_model"])
)

_session_cache = _cfg.get("session_cache", _DEFAULTS["session_cache"])
SESSION_CACHE_TTL: Final[int] = int(
    _session_cache.get("ttl_seconds", _DEFAULTS["session_cache"]["ttl_seconds"])
)

_retrieval_budget = _cfg.get("retrieval_budget", _DEFAULTS["retrieval_budget"])
MAX_RESULTS_PER_SEARCH: Final[int] = int(
    _retrieval_budget.get("max_results_per_search", _DEFAULTS["retrieval_budget"]["max_results_per_search"])
)
MAX_CONTEXT_TOKENS: Final[int] = int(
    _retrieval_budget.get("max_context_tokens", _DEFAULTS["retrieval_budget"]["max_context_tokens"])
)

__all__ = [
    "WORKING_STORAGE_URL",
    "WORKING_TTL_SECONDS",
    "EPISODIC_STORAGE_PATH",
    "EPISODIC_COLLECTION_NAME",
    "PERMANENT_LETTA_URL",
    "PERMANENT_AGENT_NAME",
    "PERMANENT_LETTA_MODEL",
    "SESSION_CACHE_TTL",
    "MAX_RESULTS_PER_SEARCH",
    "MAX_CONTEXT_TOKENS",
]
