"""benchmark.prefetch_metrics — hit@K / mean-rank aggregation for the retrieval-only benchmark (spec §4.3).

Mirrors benchmark.metrics.BenchmarkMetrics's from_results shape, scoped to
per-state retrieval quality plus a per-state breakdown of which mechanism(s)
contributed to each hit (mechanism attribution only exists for actual hits —
a miss has no mechanism to attribute).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PrefetchMetrics"]

_STATES = ("cold", "warm", "drift")


@dataclass
class PrefetchMetrics:
    """Aggregate hit@K, mean rank, and mechanism-hit counts, per turn state."""

    total_cases: int
    hit_rate_by_state: dict[str, float]
    mean_rank_by_state: dict[str, float | None]
    mechanism_hit_counts_by_state: dict[str, dict[str, int]]

    @classmethod
    def from_results(cls, results: list[dict]) -> "PrefetchMetrics":
        """Build aggregates from per-case results (see prefetch_bench.evaluate_case)."""
        hit_rate_by_state: dict[str, float] = {}
        mean_rank_by_state: dict[str, float | None] = {}
        mechanism_hit_counts_by_state: dict[str, dict[str, int]] = {}

        for state in _STATES:
            entries = [r["states"][state] for r in results if state in r.get("states", {})]
            hits = [e["hit"] for e in entries]
            ranks = [e["rank"] for e in entries if e["rank"] is not None]
            hit_rate_by_state[state] = (sum(hits) / len(hits)) if hits else 0.0
            mean_rank_by_state[state] = (sum(ranks) / len(ranks)) if ranks else None
            mech_counts: dict[str, int] = {}
            for e in entries:
                if not e["hit"]:
                    continue
                for mech in e.get("mechanisms", []):
                    mech_counts[mech] = mech_counts.get(mech, 0) + 1
            mechanism_hit_counts_by_state[state] = mech_counts

        return cls(
            total_cases=len(results),
            hit_rate_by_state=hit_rate_by_state,
            mean_rank_by_state=mean_rank_by_state,
            mechanism_hit_counts_by_state=mechanism_hit_counts_by_state,
        )
