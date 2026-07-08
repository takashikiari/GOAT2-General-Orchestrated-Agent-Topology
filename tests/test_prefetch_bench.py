"""tests.test_prefetch_bench — retrieval-only RRF pipeline benchmark (spec §4.3, §7)."""
from __future__ import annotations

import asyncio

from benchmark.prefetch_bench import evaluate_case, run_prefetch_benchmark


def _hit(message_id: str) -> dict:
    return {"content": message_id, "metadata": {"message_id": message_id}}


class _FakeLayers:
    """cold state finds the target via bm25 only; warm/drift reuse cold's activation."""

    async def search_episodic_with_cache(self, chat_id, query, limit=5, chat_id_filter=None):
        return [], False, "key"

    async def search_episodic(self, query, limit=5, topic_id=None, **kw):
        return []

    async def bm25_search(self, query, limit=15):
        return [_hit("target")]

    async def extract_query_entities(self, query):
        return {"entities": [], "entity_types": []}

    async def boost_by_entities(self, query, results, pre_extracted=None):
        return results

    async def rerank(self, query, results):
        return results

    async def bump_access(self, chat_id, ids):
        pass


def test_evaluate_case_finds_hit_via_bm25_in_cold_state():
    layers = _FakeLayers()
    case = {"id": "c1", "query": "q", "message_id": "target", "chat_id_source": "src"}
    result = asyncio.run(evaluate_case(layers, case))
    assert result["states"]["cold"] == {"hit": True, "rank": 1, "mechanisms": ["bm25"]}


def test_evaluate_case_warm_state_reuses_cold_activation():
    layers = _FakeLayers()
    case = {"id": "c1", "query": "q", "message_id": "target", "chat_id_source": "src"}
    result = asyncio.run(evaluate_case(layers, case))
    assert result["states"]["warm"]["hit"] is True


def test_evaluate_case_reports_miss_when_id_not_found():
    layers = _FakeLayers()
    case = {"id": "c2", "query": "q", "message_id": "missing", "chat_id_source": "src"}
    result = asyncio.run(evaluate_case(layers, case))
    assert result["states"]["cold"] == {"hit": False, "rank": None, "mechanisms": []}


def test_run_prefetch_benchmark_aggregates_multiple_cases():
    layers = _FakeLayers()
    cases = [
        {"id": "c1", "query": "q1", "message_id": "target", "chat_id_source": "src"},
        {"id": "c2", "query": "q2", "message_id": "missing", "chat_id_source": "src"},
    ]
    metrics = asyncio.run(run_prefetch_benchmark(cases, layers))
    assert metrics.total_cases == 2
    assert metrics.hit_rate_by_state["cold"] == 0.5
