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
        self.cache_misses = 0
        self.tier_hits: defaultdict[str, int] = defaultdict(int)
        self.intent_counts: defaultdict[str, int] = defaultdict(int)
        self.total_tokens_injected = 0
        self.total_tokens_l0_l1 = 0
        self.total_tokens_l2 = 0
        self.total_tokens_l3 = 0
        self.total_results_found = 0
        self.total_results_used = 0
        self.total_latency_classify = 0.0
        self.total_latency_search = 0.0
        self.total_latency_assemble = 0.0
        self.total_latency_inject = 0.0
        self.total_latency = 0.0

    def record(self, obs: MemoryObservation) -> None:
        """Fold a single observation into the aggregates."""
        self.total_requests += 1
        if obs.prefetch_attempted:
            self.total_prefetch_attempts += 1
            if obs.prefetch_succeeded:
                self.total_prefetch_successes += 1
            if obs.prefetch_timeout:
                self.total_prefetch_timeouts += 1
        if obs.cache_hit:
            self.cache_hits += 1
        if obs.cache_miss:
            self.cache_misses += 1
        if obs.source_tier:
            self.tier_hits[obs.source_tier] += 1
        if obs.intent_category:
            self.intent_counts[obs.intent_category] += 1
        self.total_tokens_injected += obs.tokens_injected
        self.total_tokens_l0_l1 += obs.tokens_l0_l1
        self.total_tokens_l2 += obs.tokens_l2
        self.total_tokens_l3 += obs.tokens_l3
        self.total_results_found += obs.results_found
        self.total_results_used += obs.results_used
        self.total_latency_classify += obs.latency_classify
        self.total_latency_search += obs.latency_search
        self.total_latency_assemble += obs.latency_assemble
        self.total_latency_inject += obs.latency_inject
        self.total_latency += obs.latency_total

    def get_report(self) -> dict:
        """Build the aggregated rates/averages report."""
        n = self.total_requests or 1
        attempts = max(1, self.total_prefetch_attempts)
        return {
            "total_requests": self.total_requests,
            "cache_hit_rate": self.cache_hits / n,
            "cache_miss_rate": self.cache_misses / n,
            "prefetch_attempt_rate": self.total_prefetch_attempts / n,
            "prefetch_success_rate": self.total_prefetch_successes / attempts,
            "prefetch_timeout_rate": self.total_prefetch_timeouts / attempts,
            "tier_hit_rates": {k: v / n for k, v in self.tier_hits.items()},
            "top_intents": dict(sorted(self.intent_counts.items(), key=lambda x: -x[1])[:5]),
            "avg_tokens_injected": self.total_tokens_injected / n,
            "avg_tokens_l0_l1": self.total_tokens_l0_l1 / n,
            "avg_tokens_l2": self.total_tokens_l2 / n,
            "avg_tokens_l3": self.total_tokens_l3 / n,
            "avg_results_found": self.total_results_found / n,
            "avg_results_used": self.total_results_used / n,
            "avg_latency_classify": self.total_latency_classify / n,
            "avg_latency_search": self.total_latency_search / n,
            "avg_latency_assemble": self.total_latency_assemble / n,
            "avg_latency_inject": self.total_latency_inject / n,
            "avg_latency_total": self.total_latency / n,
        }

    def log_report(self) -> None:
        """Emit the report as a structured JSON line at INFO."""
        log.info(json.dumps(self.get_report(), default=str))

    def reset(self) -> None:
        """Clear all aggregates (e.g. between benchmark runs)."""
        self._reset()