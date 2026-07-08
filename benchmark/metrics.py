"""benchmark.metrics — aggregated metrics over a benchmark run.

``BenchmarkMetrics`` is a pure dataclass aggregating the per-test result dicts
produced by ``BenchmarkRunner``. It computes accuracy, latency spread, the
session cache hit rate, L3 prefetch attempt/success/timeout counts plus a usefulness
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
from statistics import mean, pstdev
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

    grounded_correct: int = 0
    ungrounded_correct: int = 0
    grounding_fidelity: float = 0.0

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
            ("Grounded correct", self.grounded_correct),
            ("Ungrounded correct (guessed)", self.ungrounded_correct),
            ("Grounding fidelity", f"{self.grounding_fidelity * 100:.1f}%"),
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
            f"   Grounded correct: {self.grounded_correct} (fidelity {self.grounding_fidelity * 100:.0f}%)",
            f"   Ungrounded correct (guessed): {self.ungrounded_correct}",
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
        grounded_correct = sum(1 for r in results if r.get("correct") and r.get("grounded"))
        ungrounded_correct = sum(1 for r in results if r.get("correct") and not r.get("grounded"))
        denom = n or 1
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
            # Denominator is total_tests (not hits+misses) so every case is
            # counted: errored/no-outcome turns and repeat-collapsed cases
            # (both flags False) register as non-hits rather than vanishing.
            cache_hit_rate=hits / denom,
            prefetch_attempts=pf_att,
            prefetch_successes=pf_suc,
            prefetch_timeouts=pf_to,
            prefetch_usefulness=useful / pf_denom if pf_suc else 0.0,
            grounded_correct=grounded_correct,
            ungrounded_correct=ungrounded_correct,
            # Of the correct answers, how many were grounded in retrievable memory.
            grounding_fidelity=grounded_correct / correct if correct else 0.0,
            avg_tokens_injected=mean(_ints(results, "tokens_injected")),
            avg_tokens_l0_l1=mean(_ints(results, "tokens_l0_l1")),
            avg_tokens_l2=mean(_ints(results, "tokens_l2")),
            avg_tokens_l3=mean(_ints(results, "tokens_l3")),
            results_by_tier=dict(tiers),
        )


def _ints(results: list[dict], key: str) -> list[int]:
    """Extract an integer per result for ``key``, defaulting to 0."""
    return [int(r.get(key, 0) or 0) for r in results]


@dataclass
class AggregatedMetrics:
    """Mean ± population std of key metrics across N repetitions of one dataset.

    Built by ``AggregatedMetrics.from_runs`` from a list of ``BenchmarkMetrics``
    dicts (one per repetition). Std is population std (``pstdev``) — the N runs
    are the whole population of interest, not a sample.
    """

    runs: int = 0
    total_cases: int = 0
    correct_total: int = 0
    accuracy_mean: float = 0.0
    accuracy_std: float = 0.0
    avg_latency_mean: float = 0.0
    avg_latency_std: float = 0.0
    cache_hit_rate_mean: float = 0.0
    cache_hit_rate_std: float = 0.0
    prefetch_usefulness_mean: float = 0.0
    prefetch_usefulness_std: float = 0.0
    grounded_correct_mean: float = 0.0
    ungrounded_correct_mean: float = 0.0

    @classmethod
    def from_runs(cls, metric_dicts: list[dict]) -> "AggregatedMetrics":
        """Aggregate N per-run metric dicts into mean ± std. Empty input → zeros."""
        n = len(metric_dicts)
        if n == 0:
            return cls()

        def col(key: str) -> list[float]:
            return [float(d.get(key, 0.0) or 0.0) for d in metric_dicts]

        acc, lat, cache, pfu = col("accuracy"), col("avg_latency"), col("cache_hit_rate"), col("prefetch_usefulness")
        grnd, ungrnd = col("grounded_correct"), col("ungrounded_correct")
        std = pstdev if n > 1 else lambda _xs: 0.0
        return cls(
            runs=n,
            total_cases=sum(int(d.get("total_tests", 0) or 0) for d in metric_dicts),
            correct_total=sum(int(d.get("correct", 0) or 0) for d in metric_dicts),
            accuracy_mean=mean(acc), accuracy_std=std(acc),
            avg_latency_mean=mean(lat), avg_latency_std=std(lat),
            cache_hit_rate_mean=mean(cache), cache_hit_rate_std=std(cache),
            prefetch_usefulness_mean=mean(pfu), prefetch_usefulness_std=std(pfu),
            grounded_correct_mean=mean(grnd), ungrounded_correct_mean=mean(ungrnd),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly)."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def summary_lines(self, dataset: str) -> list[str]:
        """Compact aggregated report block (mean ± std)."""
        per_run = self.total_cases // max(1, self.runs)
        return [
            "📊 BENCHMARK REPORT (aggregated)",
            f"   Dataset: {dataset}",
            f"   Runs: {self.runs}",
            f"   Total cases: {self.total_cases} ({self.runs} × {per_run})",
            f"   Correct: {self.correct_total}",
            f"   Accuracy: {self.accuracy_mean * 100:.1f}% ± {self.accuracy_std * 100:.1f}%",
            f"   Avg latency: {self.avg_latency_mean:.1f}s ± {self.avg_latency_std:.1f}s",
            f"   Cache hit rate: {self.cache_hit_rate_mean * 100:.1f}% ± {self.cache_hit_rate_std * 100:.1f}%",
            f"   Prefetch usefulness: {self.prefetch_usefulness_mean * 100:.1f}% ± {self.prefetch_usefulness_std * 100:.1f}%",
            f"   Grounded correct: {self.grounded_correct_mean:.1f}",
            f"   Ungrounded correct (guessed): {self.ungrounded_correct_mean:.1f}",
        ]