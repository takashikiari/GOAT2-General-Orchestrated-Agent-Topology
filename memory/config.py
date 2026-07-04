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
    },
    "prefetch": {
        # 1.0s (was 0.5s): the benchmark cache dataset showed L2.5 cache hit rate
        # capped at ~72% ± 18.7% because ChromaDB semantic search frequently
        # exceeds 0.5s, cancelling the prefetch daemon before it can populate the
        # L2.5 cache. 1.0s lets more searches complete → more cache entries on the
        # repeat turn, without unbounded latency (still capped at 1.0s/turn).
        "timeout": 1.0,
        "max_results": 15,
        "recency_window_days": 30,
        "access_count_ref": 10,
        "score_similarity_weight": 0.6,
        "score_recency_weight": 0.3,
        "score_access_weight": 0.1,
    },
    "analytics": {
        "log_interval": 100,
    },
    # L2.5 brain-activation layer — per-chat thread state. See config/memory.toml
    # [activation]; time is cleanup only (not a reset), a thread breaks only on
    # a consensus shift (drift AND lexical overlap both drop), and an on-thread
    # write refreshes the activation in place.
    "activation": {
        "ttl_seconds": 604800,
        "drift_warm": 0.80,
        "drift_cold": 0.55,
        "lexical_low": 0.15,
        "enriching_sim": 0.55,
        "lexical_window": 5,
    },
    # Agentic tool-calling loop — see config/memory.toml [tool_loop]. The cap is
    # a hard backstop (bounds cost/latency per turn, kills a runaway loop), never
    # a grounding decider. Below the cap the model is called with tools so it can
    # chain; at the cap tools are withheld and synthesis is forced.
    "tool_loop": {
        "max_iterations": 6,
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
L3_GAP_SIGNIFICANCE: Final[float] = float(
    _retrieval_budget.get("l3_gap_significance", _DEFAULTS["retrieval_budget"].get("l3_gap_significance", 3.0))
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

# Prefetch daemon config — see config/memory.toml [prefetch]. timeout is the
# only blocker (no confidence gate); max_results caps the per-turn result count.
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
PREFETCH_SCORE_SIMILARITY_WEIGHT: Final[float] = float(
    _prefetch.get("score_similarity_weight", _DEFAULTS["prefetch"]["score_similarity_weight"])
)
PREFETCH_SCORE_RECENCY_WEIGHT: Final[float] = float(
    _prefetch.get("score_recency_weight", _DEFAULTS["prefetch"]["score_recency_weight"])
)
PREFETCH_SCORE_ACCESS_WEIGHT: Final[float] = float(
    _prefetch.get("score_access_weight", _DEFAULTS["prefetch"]["score_access_weight"])
)

_analytics = _cfg.get("analytics", _DEFAULTS["analytics"])
ANALYTICS_LOG_INTERVAL: Final[int] = int(
    _analytics.get("log_interval", _DEFAULTS["analytics"]["log_interval"])
)

# Activation layer config — see config/memory.toml [activation]. TTL is a
# cleanup horizon (NOT a reset); drift_*_warm/cold + lexical_low define the
# consensus-shift rule; enriching_sim gates the on-thread write refresh.
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

# Agentic tool-calling loop — see config/memory.toml [tool_loop].
_tool_loop = _cfg.get("tool_loop", _DEFAULTS["tool_loop"])
AGENTIC_MAX_ITERATIONS: Final[int] = int(
    _tool_loop.get("max_iterations", _DEFAULTS["tool_loop"]["max_iterations"])
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
    "L3_GAP_SIGNIFICANCE",
    "BUDGET_BASE",
    "BUDGET_CONFIDENCE_MULTIPLIER",
    "BUDGET_COMPLEXITY_MAX_BONUS",
    "BUDGET_HARD_CAP",
    "PREFETCH_TIMEOUT",
    "PREFETCH_MAX_RESULTS",
    "PREFETCH_RECENCY_WINDOW_DAYS",
    "PREFETCH_ACCESS_COUNT_REF",
    "PREFETCH_SCORE_SIMILARITY_WEIGHT",
    "PREFETCH_SCORE_RECENCY_WEIGHT",
    "PREFETCH_SCORE_ACCESS_WEIGHT",
    "ANALYTICS_LOG_INTERVAL",
    "ACTIVATION_TTL_SECONDS",
    "ACTIVATION_DRIFT_WARM",
    "ACTIVATION_DRIFT_COLD",
    "ACTIVATION_LEXICAL_LOW",
    "ACTIVATION_ENRICHING_SIM",
    "ACTIVATION_LEXICAL_WINDOW",
    "AGENTIC_MAX_ITERATIONS",
]
