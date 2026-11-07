"""memory.config — memory tier constants read from config/memory.toml."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

from memory.config_defaults import _DEFAULTS

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "memory.toml"


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS
    from memory.config_validator import validate_config
    validate_config(cfg)
    return cfg


_cfg = _load()

_working = _cfg.get("working", _DEFAULTS["working"])
WORKING_STORAGE_URL: str = str(
    _working.get("storage_url", _DEFAULTS["working"]["storage_url"])
)
WORKING_TTL_SECONDS: int = int(
    _working.get("ttl_seconds", _DEFAULTS["working"]["ttl_seconds"])
)
WORKING_MAX_MESSAGES: Final[int] = int(
    _working.get("max_messages", _DEFAULTS["working"]["max_messages"])
)

_episodic = _cfg.get("episodic", _DEFAULTS["episodic"])
EPISODIC_STORAGE_PATH: str = str(
    _episodic.get("storage_path", _DEFAULTS["episodic"]["storage_path"])
)
EPISODIC_COLLECTION_NAME: str = str(
    _episodic.get("collection_name", _DEFAULTS["episodic"]["collection_name"])
)


def _ep(key: str):
    return _episodic.get(key, _DEFAULTS["episodic"][key])


# Phase 1 (memory spec v4): chunked L3 in a separate store. chunks_write keeps it in sync with every
# L3 write/enrichment/delete; chunks_search makes retrieval use it. Rollback = chunks_search = false.
CHUNK_STORAGE_PATH: str = str(_ep("chunk_storage_path"))
CHUNK_COLLECTION_NAME: str = str(_ep("chunk_collection_name"))
CHUNK_TARGET_TOKENS: Final[int] = int(_ep("chunk_target_tokens"))
CHUNK_OVERLAP_TOKENS: Final[int] = int(_ep("chunk_overlap_tokens"))
CHUNK_NEIGHBOR_RADIUS: Final[int] = int(_ep("chunk_neighbor_radius"))
CHUNKS_WRITE: Final[bool] = bool(_ep("chunks_write"))
CHUNKS_SEARCH: Final[bool] = bool(_ep("chunks_search"))
# highlights (chosen after the offline evaluation): candidates are still found on whole documents, each is
# swapped for its closest chunk only for the rerank, then given the ±1 window. Needs the chunk store.
CHUNKS_HIGHLIGHTS: Final[bool] = bool(_ep("chunks_highlights"))
# Phase 2 (memory spec v4): wave 2 — the top seeds pull in same-topic neighbours (sequence_number ±window).
L3_WAVE2: Final[bool] = bool(_ep("l3_wave2"))
L3_WAVE2_SEEDS: Final[int] = int(_ep("l3_wave2_seeds"))
L3_WAVE2_PER_SEED: Final[int] = int(_ep("l3_wave2_per_seed"))
L3_WAVE2_WINDOW: Final[int] = int(_ep("l3_wave2_window"))
# T7: L3 entries with fewer letters/digits than this ("🤦", "Ce?") never become rerank candidates. 0 = off.
L3_MIN_ALNUM: Final[int] = int(_ep("l3_min_alnum"))

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
TEMPORAL_L3_GUARANTEE_TOKENS: Final[int] = int(
    _retrieval_budget.get(
        "temporal_l3_guarantee_tokens", _DEFAULTS["retrieval_budget"]["temporal_l3_guarantee_tokens"]
    )
)
L3_GAP_SIGNIFICANCE: Final[float] = float(
    _retrieval_budget.get("l3_gap_significance", _DEFAULTS["retrieval_budget"]["l3_gap_significance"])
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

_prefetch = _cfg.get("prefetch", _DEFAULTS["prefetch"])
PREFETCH_TIMEOUT: Final[float] = float(
    _prefetch.get("timeout", _DEFAULTS["prefetch"]["timeout"])
)
PREFETCH_MAX_RESULTS: Final[int] = int(
    _prefetch.get("max_results", _DEFAULTS["prefetch"]["max_results"])
)
PREFETCH_RECENCY_WINDOW_DAYS: Final[int] = int(
    _prefetch.get("recency_window_days", _DEFAULTS["prefetch"]["recency_window_days"])
)
PREFETCH_ACCESS_COUNT_REF: Final[int] = int(
    _prefetch.get("access_count_ref", _DEFAULTS["prefetch"]["access_count_ref"])
)
PREFETCH_RECENCY_BASE_WEIGHT: Final[float] = float(
    _prefetch.get("recency_base_weight", _DEFAULTS["prefetch"]["recency_base_weight"])
)
PREFETCH_RECENCY_RECENCY_WEIGHT: Final[float] = float(
    _prefetch.get("recency_recency_weight", _DEFAULTS["prefetch"]["recency_recency_weight"])
)
_analytics = _cfg.get("analytics", _DEFAULTS["analytics"])
ANALYTICS_LOG_INTERVAL: Final[int] = int(
    _analytics.get("log_interval", _DEFAULTS["analytics"]["log_interval"])
)

_activation_cfg = _cfg.get("activation", _DEFAULTS["activation"])
ACTIVATION_TTL_SECONDS: Final[int] = int(
    _activation_cfg.get("ttl_seconds", _DEFAULTS["activation"]["ttl_seconds"])
)
ACTIVATION_DRIFT_WARM: Final[float] = float(
    _activation_cfg.get("drift_warm", _DEFAULTS["activation"]["drift_warm"])
)
ACTIVATION_DRIFT_COLD: Final[float] = float(
    _activation_cfg.get("drift_cold", _DEFAULTS["activation"]["drift_cold"])
)
ACTIVATION_LEXICAL_LOW: Final[float] = float(
    _activation_cfg.get("lexical_low", _DEFAULTS["activation"]["lexical_low"])
)
ACTIVATION_ENRICHING_SIM: Final[float] = float(
    _activation_cfg.get("enriching_sim", _DEFAULTS["activation"]["enriching_sim"])
)
ACTIVATION_LEXICAL_WINDOW: Final[int] = int(
    _activation_cfg.get("lexical_window", _DEFAULTS["activation"]["lexical_window"])
)
ACTIVATION_MIN_WORDS_COLD: Final[int] = int(
    _activation_cfg.get("min_words_cold", _DEFAULTS["activation"]["min_words_cold"])
)
TOPIC_RETURN_THRESHOLD: Final[float] = float(
    _activation_cfg.get("topic_return_threshold", _DEFAULTS["activation"]["topic_return_threshold"])
)
TOPIC_ARCHIVE_MAX: Final[int] = int(
    _activation_cfg.get("topic_archive_max", _DEFAULTS["activation"]["topic_archive_max"])
)

_reranker_cfg = _cfg.get("reranker", _DEFAULTS["reranker"])
RERANKER_ENABLED: Final[bool] = bool(
    _reranker_cfg.get("enabled", _DEFAULTS["reranker"]["enabled"])
)
RERANKER_MODEL: Final[str] = str(
    _reranker_cfg.get("model", _DEFAULTS["reranker"]["model"])
)
RERANKER_TOP_K: Final[int] = int(
    _reranker_cfg.get("top_k", _DEFAULTS["reranker"]["top_k"])
)

__all__ = [
    "WORKING_STORAGE_URL",
    "WORKING_TTL_SECONDS",
    "EPISODIC_STORAGE_PATH",
    "EPISODIC_COLLECTION_NAME",
    "CHUNK_STORAGE_PATH",
    "CHUNK_COLLECTION_NAME",
    "CHUNK_TARGET_TOKENS",
    "CHUNK_OVERLAP_TOKENS",
    "CHUNK_NEIGHBOR_RADIUS",
    "CHUNKS_WRITE",
    "CHUNKS_SEARCH",
    "CHUNKS_HIGHLIGHTS",
    "L3_WAVE2",
    "L3_WAVE2_SEEDS",
    "L3_WAVE2_PER_SEED",
    "L3_WAVE2_WINDOW",
    "L3_MIN_ALNUM",
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
    "TEMPORAL_L3_GUARANTEE_TOKENS",
    "L3_GAP_SIGNIFICANCE",
    "BUDGET_BASE",
    "BUDGET_CONFIDENCE_MULTIPLIER",
    "BUDGET_COMPLEXITY_MAX_BONUS",
    "BUDGET_HARD_CAP",
    "PREFETCH_TIMEOUT",
    "PREFETCH_MAX_RESULTS",
    "PREFETCH_RECENCY_WINDOW_DAYS",
    "PREFETCH_ACCESS_COUNT_REF",
    "PREFETCH_RECENCY_BASE_WEIGHT",
    "PREFETCH_RECENCY_RECENCY_WEIGHT",
    "ANALYTICS_LOG_INTERVAL",
    "ACTIVATION_TTL_SECONDS",
    "ACTIVATION_DRIFT_WARM",
    "ACTIVATION_DRIFT_COLD",
    "ACTIVATION_LEXICAL_LOW",
    "ACTIVATION_ENRICHING_SIM",
    "ACTIVATION_LEXICAL_WINDOW",
    "ACTIVATION_MIN_WORDS_COLD",
    "TOPIC_RETURN_THRESHOLD",
    "TOPIC_ARCHIVE_MAX",
    "RERANKER_ENABLED",
    "RERANKER_MODEL",
    "RERANKER_TOP_K",
]
