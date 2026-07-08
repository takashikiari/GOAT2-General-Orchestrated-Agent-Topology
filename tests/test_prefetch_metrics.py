"""tests.test_prefetch_metrics — hit@K / mean-rank aggregation (spec §4.3)."""
from __future__ import annotations

from benchmark.prefetch_metrics import PrefetchMetrics


def test_from_results_computes_hit_rate_and_mean_rank_per_state():
    results = [
        {"case_id": "1", "states": {
            "cold": {"hit": True, "rank": 1, "mechanisms": ["bm25"]},
            "warm": {"hit": True, "rank": 1, "mechanisms": ["prediction"]},
            "drift": {"hit": False, "rank": None, "mechanisms": []},
        }},
        {"case_id": "2", "states": {
            "cold": {"hit": False, "rank": None, "mechanisms": []},
            "warm": {"hit": True, "rank": 2, "mechanisms": ["semantic_global", "bm25"]},
            "drift": {"hit": True, "rank": 1, "mechanisms": ["temporal"]},
        }},
    ]
    m = PrefetchMetrics.from_results(results)
    assert m.total_cases == 2
    assert m.hit_rate_by_state["cold"] == 0.5
    assert m.hit_rate_by_state["warm"] == 1.0
    assert m.hit_rate_by_state["drift"] == 0.5
    assert m.mean_rank_by_state["cold"] == 1.0
    assert m.mean_rank_by_state["warm"] == 1.5
    assert m.mean_rank_by_state["drift"] == 1.0
    assert m.mechanism_hit_counts_by_state["warm"] == {
        "prediction": 1, "semantic_global": 1, "bm25": 1,
    }


def test_from_results_handles_empty_list():
    m = PrefetchMetrics.from_results([])
    assert m.total_cases == 0
    assert m.hit_rate_by_state["cold"] == 0.0
    assert m.mean_rank_by_state["cold"] is None
    assert m.mechanism_hit_counts_by_state["cold"] == {}
