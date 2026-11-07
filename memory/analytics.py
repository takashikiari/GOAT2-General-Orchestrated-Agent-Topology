"""
memory.analytics — aggregated metrics over memory observations.

``MemoryAnalytics`` accumulates ``MemoryObservation`` records (one per request)
and produces a report of rates and averages: cache hit rate, prefetch
attempt/success/timeout rates, tier hit rates, top intents, average tokens per
tier, average results, and average latency per stage. It is **registry-owned**
(instantiated lazily by ``ServiceRegistry``), not a module singleton — the
zero-singleton rule holds. The orchestrator calls ``record`` each turn and
``log_report`` every ``ANALYTICS_LOG_INTERVAL`` requests.
"""
from __future__ import annotations

import json
from collections import defaultdict

from memory.observability import MemoryObservation
from utils.logging.setup import get_logger

log = get_logger(__name__)


class MemoryAnalytics:
    """Aggregated analytics over memory operations (registry-owned)."""

    def __init__(self) -> None:
        """Start with empty aggregates."""
        self._reset()

    def _reset(self) -> None:
        """Zero every aggregate counter/sum."""
        self.total_requests = 0
        self.total_prefetch_attempts = 0
        self.total_prefetch_successes = 0
        self.total_prefetch_timeouts = 0
        self.cache_hits = 0
        self.cache_lookups = 0  # turns whose retrieve() actually consulted the search cache (cold turns)
        self.cache_misses = 0
        self.tier_hits: defaultdict[str, int] = defaultdict(int)
        # Multi-label: how many turns had EACH tier present, not just the
        # single highest-priority one — see MemoryObservation.tiers_used.
        self.tier_presence: defaultdict[str, int] = defaultdict(int)
        self.intent_counts: defaultdict[str, int] = defaultdict(int)
        self.total_tokens_injected = 0
        self.total_tokens_l0_l1 = 0
        self.total_tokens_l2 = 0
        self.total_tokens_l3 = 0
        # Breakdown of the prompt weight that is not memory (Laris, 2026-11-06): guidance text and tool schemas
        # are folded into tokens_injected; the rest of tokens_prompt_api is the context re-sent on every extra
        # LLM call of a tool round plus the tool results themselves.
        self.total_tokens_guidance = 0
        self.total_tokens_tools = 0
        self.total_tokens_resent_est = 0
        self.total_tokens_tool_results_est = 0
        self.total_results_found = 0
        self.total_results_used = 0
        self.total_latency_classify = 0.0
        self.total_latency_search = 0.0
        self.total_latency_assemble = 0.0
        self.total_latency_inject = 0.0
        self.total_latency_llm = 0.0
        self.total_latency_llm_first = 0.0
        self.total_latency_tool_rounds = 0.0
        self.total_latency_save = 0.0
        self.total_latency = 0.0
        # Real API token usage (billed, from response.usage)
        self.total_tokens_prompt_api = 0
        self.total_tokens_completion_api = 0
        self.total_tokens_total_api = 0
        self.total_llm_calls = 0
        # L2.5 activation (brain thread state) — see memory.activation.
        self.cold_turns = 0
        self.warm_turns = 0
        self.drift_turns = 0
        self.thread_breaks = 0
        self.enriching_writes = 0
        self.filing_writes = 0
        self.enriching_refreshes = 0
        # L2.5 prefetch mechanism breakdown
        self.warm_served_turns = 0
        self.total_prefetch_thematic = 0
        self.total_prefetch_specific_key = 0

    def record(self, obs: MemoryObservation) -> None:
        """Fold a single observation into the aggregates."""
        self.total_requests += 1
        if obs.prefetch_attempted:
            self.total_prefetch_attempts += 1
            if obs.prefetch_succeeded:
                self.total_prefetch_successes += 1
            if obs.prefetch_timeout:
                self.total_prefetch_timeouts += 1
        if obs.cache_key:  # a key is reported only when the cache was really looked up
            self.cache_lookups += 1
        if obs.cache_hit:
            self.cache_hits += 1
        if obs.cache_miss:
            self.cache_misses += 1
        if obs.source_tier:
            self.tier_hits[obs.source_tier] += 1
        for tier in obs.tiers_used:
            self.tier_presence[tier] += 1
        if obs.intent_category:
            self.intent_counts[obs.intent_category] += 1
        self.total_tokens_injected += obs.tokens_injected
        self.total_tokens_l0_l1 += obs.tokens_l0_l1
        self.total_tokens_l2 += obs.tokens_l2
        self.total_tokens_l3 += obs.tokens_l3
        self.total_tokens_guidance += obs.tokens_guidance
        self.total_tokens_tools += obs.tokens_tools
        # Each LLM call re-sends the injected context, so calls 2..k cost (k-1) × injected; what is left of the
        # API prompt after k × injected is the tool results carried between rounds. Estimates, never negative.
        calls = max(1, int(obs.llm_calls or 1))
        self.total_tokens_resent_est += (calls - 1) * obs.tokens_injected
        self.total_tokens_tool_results_est += max(0, obs.tokens_prompt_api - calls * obs.tokens_injected)
        self.total_results_found += obs.results_found
        self.total_results_used += obs.results_used
        self.total_latency_classify += obs.latency_classify
        self.total_latency_search += obs.latency_search
        self.total_latency_assemble += obs.latency_assemble
        self.total_latency_inject += obs.latency_inject
        self.total_latency_llm += obs.latency_llm
        self.total_latency_llm_first += obs.latency_llm_first
        self.total_latency_tool_rounds += obs.latency_tool_rounds
        self.total_latency_save += obs.latency_save
        self.total_latency += obs.latency_total
        self.total_tokens_prompt_api += obs.tokens_prompt_api
        self.total_tokens_completion_api += obs.tokens_completion_api
        self.total_tokens_total_api += obs.tokens_total_api
        self.total_llm_calls += obs.llm_calls
        # L2.5 activation counters — one per turn-state / write-kind.
        if obs.activation_state == "cold":
            self.cold_turns += 1
        elif obs.activation_state == "warm":
            self.warm_turns += 1
        elif obs.activation_state == "drift":
            self.drift_turns += 1
        if obs.thread_break:
            self.thread_breaks += 1
        if obs.write_kind == "enriching":
            self.enriching_writes += 1
        elif obs.write_kind == "filing":
            self.filing_writes += 1
        if obs.enriching_refresh:
            self.enriching_refreshes += 1
        if obs.warm_served:
            self.warm_served_turns += 1
        self.total_prefetch_thematic += obs.prefetch_thematic_count
        self.total_prefetch_specific_key += obs.prefetch_specific_key_count

    def get_report(self) -> dict:
        """Build the aggregated rates/averages report."""
        n = self.total_requests or 1
        attempts = max(1, self.total_prefetch_attempts)
        return {
            "total_requests": self.total_requests,
            "cache_hit_rate": self.cache_hits / n,
            # cache_hit_rate is over ALL turns; only cold turns consult the cache, so read the hit rate
            # against the lookups actually performed — 0.0 on few lookups means "rarely consulted",
            # not a broken cache.
            "cache_lookups": self.cache_lookups,
            "cache_lookup_rate": self.cache_lookups / n,
            "cache_hit_rate_on_lookups": self.cache_hits / self.cache_lookups if self.cache_lookups else None,
            "cache_miss_rate": self.cache_misses / n,
            "prefetch_attempt_rate": self.total_prefetch_attempts / n,
            "prefetch_success_rate": self.total_prefetch_successes / attempts,
            "prefetch_timeout_rate": self.total_prefetch_timeouts / attempts,
            # Multi-label: fraction of turns where EACH tier's block was
            # present (a turn commonly has more than one, so these can sum
            # past 100%) — not the old single "highest priority tier" view.
            "tier_hit_rates": {k: v / n for k, v in self.tier_presence.items()},
            "top_intents": dict(sorted(self.intent_counts.items(), key=lambda x: -x[1])[:5]),
            "avg_tokens_injected": self.total_tokens_injected / n,
            "avg_tokens_l0_l1": self.total_tokens_l0_l1 / n,
            "avg_tokens_l2": self.total_tokens_l2 / n,
            "avg_tokens_l3": self.total_tokens_l3 / n,
            # New fields only (existing ones unchanged). other = API prompt − memory tiers (L0/L1 + L2 + L3).
            "avg_tokens_other": (self.total_tokens_prompt_api - self.total_tokens_l0_l1
                                 - self.total_tokens_l2 - self.total_tokens_l3) / n,
            "avg_tokens_guidance": self.total_tokens_guidance / n,
            "avg_tokens_tools": self.total_tokens_tools / n,
            "avg_tokens_injected_unattributed": (self.total_tokens_injected - self.total_tokens_l0_l1
                                                 - self.total_tokens_l2 - self.total_tokens_l3
                                                 - self.total_tokens_guidance - self.total_tokens_tools) / n,
            "avg_tokens_resent_est": self.total_tokens_resent_est / n,
            "avg_tokens_tool_results_est": self.total_tokens_tool_results_est / n,
            "avg_prompt_tokens_per_llm_call": (self.total_tokens_prompt_api / self.total_llm_calls
                                               if self.total_llm_calls else 0.0),
            "avg_results_found": self.total_results_found / n,
            "avg_results_used": self.total_results_used / n,
            "avg_latency_classify": self.total_latency_classify / n,
            "avg_latency_search": self.total_latency_search / n,
            "avg_latency_assemble": self.total_latency_assemble / n,
            "avg_latency_inject": self.total_latency_inject / n,
            "avg_latency_llm": self.total_latency_llm / n,
            "avg_latency_llm_first": self.total_latency_llm_first / n,
            "avg_latency_tool_rounds": self.total_latency_tool_rounds / n,
            "avg_latency_save": self.total_latency_save / n,
            "avg_latency_total": self.total_latency / n,
            "avg_tokens_prompt_api": self.total_tokens_prompt_api / n,
            "avg_tokens_completion_api": self.total_tokens_completion_api / n,
            "avg_tokens_total_api": self.total_tokens_total_api / n,
            "avg_llm_calls": self.total_llm_calls / n,
            "activation_cold_rate": self.cold_turns / n,
            "activation_warm_rate": self.warm_turns / n,
            "activation_drift_rate": self.drift_turns / n,
            "thread_breaks": self.thread_breaks,
            "enriching_writes": self.enriching_writes,
            "filing_writes": self.filing_writes,
            "enriching_refreshes": self.enriching_refreshes,
            "warm_served_rate": self.warm_served_turns / n,
            "avg_prefetch_thematic": self.total_prefetch_thematic / n,
            "avg_prefetch_specific_key": self.total_prefetch_specific_key / n,
        }

    def log_report(self) -> None:
        """Emit the report as a structured JSON line at INFO."""
        log.info(json.dumps(self.get_report(), default=str))

    def reset(self) -> None:
        """Clear all aggregates (e.g. between benchmark runs)."""
        self._reset()