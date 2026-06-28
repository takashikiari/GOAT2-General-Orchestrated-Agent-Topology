"""memory.config — memory tier config (working/episodic/permanent/session_cache/retrieval_budget/aits/analytics). Reads config/memory.toml."""
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
        "l1_facts_max_tokens": 500,
    },
    "session_cache": {
        "ttl_seconds": 300,
    },
    "identity": {
        "base_prompt": "You are a helpful assistant.",
    },
    "retrieval_budget": {
        "max_results_per_search": 15,
        "max_context_tokens": 4000,
        "l2_context_cap": 8000,
        "l3_reserve_fraction": 0.3,
        "l2_floor_tokens": 500,
        "l3_min_guarantee_tokens": 1200,
        "l3_similarity_max_distance": 1.0,
    },
    "aits": {
        "budget_base": 2000,
        "budget_confidence_multiplier": 4000,
        "budget_complexity_max_bonus": 2000,
        "budget_hard_cap": 12000,
        "prefetch_timeout": 0.5,
        "prefetch_confidence_threshold": 0.4,
    },
    "analytics": {
        "log_interval": 100,
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
L1_FACTS_MAX_TOKENS: Final[int] = int(
    _permanent.get("l1_facts_max_tokens", _DEFAULTS["permanent"]["l1_facts_max_tokens"])
)

_session_cache = _cfg.get("session_cache", _DEFAULTS["session_cache"])
SESSION_CACHE_TTL: Final[int] = int(
    _session_cache.get("ttl_seconds", _DEFAULTS["session_cache"]["ttl_seconds"])
)

_identity = _cfg.get("identity", _DEFAULTS["identity"])
IDENTITY_BASE_PROMPT: str = str(
    _identity.get("base_prompt", _DEFAULTS["identity"]["base_prompt"])
)

_retrieval_budget = _cfg.get("retrieval_budget", _DEFAULTS["retrieval_budget"])
MAX_RESULTS_PER_SEARCH: Final[int] = int(
    _retrieval_budget.get("max_results_per_search", _DEFAULTS["retrieval_budget"]["max_results_per_search"])
)
MAX_CONTEXT_TOKENS: Final[int] = int(
    _retrieval_budget.get("max_context_tokens", _DEFAULTS["retrieval_budget"]["max_context_tokens"])
)
L2_CONTEXT_CAP: Final[int] = int(
    _retrieval_budget.get("l2_context_cap", _DEFAULTS["retrieval_budget"]["l2_context_cap"])
)
L3_RESERVE_FRACTION: Final[float] = float(
    _retrieval_budget.get("l3_reserve_fraction", _DEFAULTS["retrieval_budget"]["l3_reserve_fraction"])
)
L2_FLOOR_TOKENS: Final[int] = int(
    _retrieval_budget.get("l2_floor_tokens", _DEFAULTS["retrieval_budget"]["l2_floor_tokens"])
)
L3_MIN_GUARANTEE_TOKENS: Final[int] = int(
    _retrieval_budget.get("l3_min_guarantee_tokens", _DEFAULTS["retrieval_budget"]["l3_min_guarantee_tokens"])
)
L3_SIMILARITY_MAX_DISTANCE: Final[float] = float(
    _retrieval_budget.get("l3_similarity_max_distance", _DEFAULTS["retrieval_budget"]["l3_similarity_max_distance"])
)

_aits = _cfg.get("aits", _DEFAULTS["aits"])
BUDGET_BASE: Final[int] = int(_aits.get("budget_base", _DEFAULTS["aits"]["budget_base"]))
BUDGET_CONFIDENCE_MULTIPLIER: Final[int] = int(
    _aits.get("budget_confidence_multiplier", _DEFAULTS["aits"]["budget_confidence_multiplier"])
)
BUDGET_COMPLEXITY_MAX_BONUS: Final[int] = int(
    _aits.get("budget_complexity_max_bonus", _DEFAULTS["aits"]["budget_complexity_max_bonus"])
)
BUDGET_HARD_CAP: Final[int] = int(_aits.get("budget_hard_cap", _DEFAULTS["aits"]["budget_hard_cap"]))
PREFETCH_TIMEOUT: Final[float] = float(
    _aits.get("prefetch_timeout", _DEFAULTS["aits"]["prefetch_timeout"])
)
PREFETCH_CONFIDENCE_THRESHOLD: Final[float] = float(
    _aits.get("prefetch_confidence_threshold", _DEFAULTS["aits"]["prefetch_confidence_threshold"])
)

_analytics = _cfg.get("analytics", _DEFAULTS["analytics"])
ANALYTICS_LOG_INTERVAL: Final[int] = int(
    _analytics.get("log_interval", _DEFAULTS["analytics"]["log_interval"])
)

__all__ = [
    "WORKING_STORAGE_URL",
    "WORKING_TTL_SECONDS",
    "EPISODIC_STORAGE_PATH",
    "EPISODIC_COLLECTION_NAME",
    "PERMANENT_LETTA_URL",
    "PERMANENT_AGENT_NAME",
    "PERMANENT_LETTA_MODEL",
    "L1_FACTS_MAX_TOKENS",
    "SESSION_CACHE_TTL",
    "IDENTITY_BASE_PROMPT",
    "MAX_RESULTS_PER_SEARCH",
    "MAX_CONTEXT_TOKENS",
    "L2_CONTEXT_CAP",
    "L3_RESERVE_FRACTION",
    "L2_FLOOR_TOKENS",
    "L3_MIN_GUARANTEE_TOKENS",
    "L3_SIMILARITY_MAX_DISTANCE",
    "BUDGET_BASE",
    "BUDGET_CONFIDENCE_MULTIPLIER",
    "BUDGET_COMPLEXITY_MAX_BONUS",
    "BUDGET_HARD_CAP",
    "PREFETCH_TIMEOUT",
    "PREFETCH_CONFIDENCE_THRESHOLD",
    "ANALYTICS_LOG_INTERVAL",
]
