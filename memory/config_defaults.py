"""memory.config_defaults — default values for all memory.toml sections.

Imported by memory.config so the main module stays under the line limit.
These values are used when the TOML file is absent or a key is missing.
"""
from __future__ import annotations

_DEFAULTS: dict = {
    "working": {
        "storage_url": "redis://localhost:6379/0",
        "ttl_seconds": 0,
        "max_messages": 20,
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
        # l3_gap_significance default — no l3_similarity_max_distance (removed).
        "l3_gap_significance": 3.0,
    },
    "aits": {
        "budget_base": 2000,
        "budget_confidence_multiplier": 4000,
        "budget_complexity_max_bonus": 2000,
        "budget_hard_cap": 12000,
    },
    "prefetch": {
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
    "activation": {
        "ttl_seconds": 604800,
        "drift_warm": 0.80,
        "drift_cold": 0.55,
        "lexical_low": 0.15,
        "enriching_sim": 0.55,
        "lexical_window": 5,
        "topic_return_threshold": 0.75,
        "topic_archive_max": 10,
    },
    "tool_loop": {
        "max_iterations": 6,
    },
    "reranker": {
        "enabled": True,
        "model": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "top_k": 20,
    },
}
