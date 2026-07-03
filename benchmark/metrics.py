"""benchmark.metrics — aggregated metrics over a benchmark run.

``BenchmarkMetrics`` is a pure dataclass aggregating the per-test result dicts
produced by ``BenchmarkRunner``. It computes accuracy, latency spread, the L2.5
cache hit rate, L3 prefetch attempt/success/timeout counts plus a usefulness
ratio, and average tokens injected per tier. It operates on plain dicts with no
imports from ``orchestrator`` or ``memory`` — importable and unit-testable with
no services running.

Per-test result dict fields it reads (all optional, zero-safe):
    correct, latency, cache_hit, cache_miss, prefetch_attempted,
    prefetch_succeeded, prefetch_timeout, results_used, tokens_injected,
    tokens_l0_l1, tokens_l2, tokens_l3, source_tier.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, fields
from statistics import mean
from typing import Any

from utils.logging.setup import get_logger

log = get_logger(__name__)


@dataclass
class BenchmarkMetrics:
    """Aggregated metrics over one dataset's worth of per-test results."""

    total_tests: int = 0
    correct: int = 0
    accuracy: float = 0.0
    avg_latency: float = 0.0
    min_latency: float = 0.0
    max_latency: float = 0.0

    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate: float = 0.0

    prefetch_attempts: int = 0
    prefetch_successes: int = 0
    prefetch_timeouts: int = 0
    prefetch_usefulness: float = 0.0

    avg_tokens_injected: float = 0.0
    avg_tokens_l0_l1: float = 0.0
    avg_tokens_l2: float = 0.0
    avg_tokens_l3: float = 0.0

    results_by_tier: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly)."""
        out: dict[str, Any] = {}
        for f in fields(self):
            out[f.name] = getattr(self, f.name)
        return out

    def to_markdown(self) -> str:
        """Format every metric as a two-column markdown table (key | value)."""
        rows = [
            ("Total tests", self.total_tests),
            ("Correct", self.correct),
            ("Accuracy", f"{self.accuracy * 100:.1f}%"),
            ("Avg latency", f"{self.avg_latency:.2f}s"),
            ("Min latency", f"{self.min_latency:.2f}s"),
            ("Max latency", f"{self.max_latency:.2f}s"),
            ("Cache hits", self.cache_hits),
            ("Cache misses", self.cache_misses),
            ("Cache hit rate", f"{self.cache_hit_rate * 100:.1f}%"),
            ("Prefetch attempts", self.prefetch_attempts),
            ("Prefetch successes", self.prefetch_successes),
            ("Prefetch timeouts", self.prefetch_timeouts),
            ("Prefetch usefulness", f"{self.prefetch_usefulness * 100:.1f}%"),
            ("Avg tokens injected", f"{self.avg_tokens_injected:.0f}"),
            ("Avg tokens L0+L1", f"{self.avg_tokens_l0_l1:.0f}"),
            ("Avg tokens L2", f"{self.avg_tokens_l2:.0f}"),
            ("Avg tokens L3", f"{self.avg_tokens_l3:.0f}"),
            ("Results by tier", ", ".join(f"{k}={v}" for k, v in self.results_by_tier.items()) or "—"),
        ]
        width = max(len(k) for k, _ in rows)
        lines = ["| " + "Metric".ljust(width) + " | Value |", "|" + "-" * (width + 2) + "|-------|"]
        for key, val in rows:
            lines.append(f"| {key.ljust(width)} | {val} |")
        return "\n".join(lines)

    def summary_lines(self, dataset: str) -> list[str]:
        """Compact human-readable block matching the canonical report format."""
        return [
            "📊 BENCHMARK REPORT",
            f"   Dataset: {dataset}",
            f"   Total tests: {self.total_tests}",
            f"   Correct: {self.correct}",
            f"   Accuracy: {self.accuracy * 100:.1f}%",
            f"   Avg latency: {self.avg_latency:.1f}s",
            f"   Cache hit rate: {self.cache_hit_rate * 100:.1f}%",
            f"   Prefetch usefulness: {self.prefetch_usefulness * 100:.1f}%",
        ]

    @classmethod
    def from_results(cls, results: list[dict]) -> "BenchmarkMetrics":
        """Aggregate a list of per-test result dicts into metrics.

        Empty input yields an all-zero ``BenchmarkMetrics``.
        """
        n = len(results)
        if n == 0:
            return cls()
        correct = sum(1 for r in results if r.get("correct"))
        lat = [float(r.get("latency", 0.0) or 0.0) for r in results]
        hits = sum(1 for r in results if r.get("cache_hit"))
        misses = sum(1 for r in results if r.get("cache_miss"))
        pf_att = sum(1 for r in results if r.get("prefetch_attempted"))
        pf_suc = sum(1 for r in results if r.get("prefetch_succeeded"))
        pf_to = sum(1 for r in results if r.get("prefetch_timeout"))
        # Usefulness: of the turns where prefetch succeeded, the fraction that
        # actually injected L3 context (results_used > 0).
        useful = sum(
            1 for r in results
            if r.get("prefetch_succeeded") and int(r.get("results_used", 0) or 0) > 0
        )
        tiers: dict[str, int] = defaultdict(int)
        for r in results:
            tier = r.get("source_tier") or "none"
            tiers[tier] += 1
        denom = n or 1
        cache_denom = hits + misses
        pf_denom = pf_suc or 1
        return cls(
            total_tests=n,
            correct=correct,
            accuracy=correct / denom,
            avg_latency=mean(lat),
            min_latency=min(lat),
            max_latency=max(lat),
            cache_hits=hits,
            cache_misses=misses,
            cache_hit_rate=hits / cache_denom if cache_denom else 0.0,
            prefetch_attempts=pf_att,
            prefetch_successes=pf_suc,
            prefetch_timeouts=pf_to,
            prefetch_usefulness=useful / pf_denom if pf_suc else 0.0,
            avg_tokens_injected=mean(_ints(results, "tokens_injected")),
            avg_tokens_l0_l1=mean(_ints(results, "tokens_l0_l1")),
            avg_tokens_l2=mean(_ints(results, "tokens_l2")),
            avg_tokens_l3=mean(_ints(results, "tokens_l3")),
            results_by_tier=dict(tiers),
        )


def _ints(results: list[dict], key: str) -> list[int]:
    """Extract an integer per result for ``key``, defaulting to 0."""
    return [int(r.get(key, 0) or 0) for r in results]