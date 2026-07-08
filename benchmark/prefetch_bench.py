"""benchmark.prefetch_bench — retrieval-only RRF pipeline benchmark (spec §4.3).

No LLM call: runs memory.retrieval.retrieve() directly in cold/warm/drift
states against a mined case's query, checking whether the ground-truth
message_id was retrieved, at what rank, and via which mechanism(s) — the
mechanisms field is carried by merge_results (memory/result_merger.py) as of
the prior review pass, so no extra plumbing is needed here.
"""
from __future__ import annotations

from benchmark.prefetch_metrics import PrefetchMetrics
from memory.activation import Activation
from memory.retrieval import retrieve

__all__ = ["evaluate_case", "run_prefetch_benchmark"]


def _score_hit(merged: list[dict], expected_message_id: str) -> dict:
    """Rank (1-indexed) + mechanisms of the ground-truth entry within ``merged``, if present."""
    for rank, r in enumerate(merged, start=1):
        if r.get("metadata", {}).get("message_id") == expected_message_id:
            return {"hit": True, "rank": rank, "mechanisms": r.get("mechanisms", [])}
    return {"hit": False, "rank": None, "mechanisms": []}


async def evaluate_case(layers, case: dict) -> dict:
    """Run one mined case through cold/warm/drift retrieve() and score each state.

    warm/drift reuse the SAME activation, built from the case's own cold-state
    result — this exercises the RRF/rescoring mechanics directly (spec goal 1),
    not a full multi-turn simulation (that is conversation_runner.run_conversation).
    """
    chat_id = case.get("chat_id_source") or "prefetch-bench"
    query = case["query"]
    expected_id = case["message_id"]

    cold_merged, *_ = await retrieve(layers, chat_id, query, "cold", None)
    activation = Activation(merged=cold_merged, topic_id="prefetch-bench-topic")

    warm_merged, *_ = await retrieve(layers, chat_id, query, "warm", activation)
    drift_merged, *_ = await retrieve(layers, chat_id, query, "drift", activation)

    return {
        "case_id": case.get("id"),
        "states": {
            "cold": _score_hit(cold_merged, expected_id),
            "warm": _score_hit(warm_merged, expected_id),
            "drift": _score_hit(drift_merged, expected_id),
        },
    }


async def run_prefetch_benchmark(cases: list[dict], layers) -> PrefetchMetrics:
    """Evaluate every case and aggregate into a PrefetchMetrics report."""
    results = [await evaluate_case(layers, case) for case in cases]
    return PrefetchMetrics.from_results(results)
