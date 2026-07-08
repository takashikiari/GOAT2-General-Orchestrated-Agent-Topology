"""tests.test_run_real_data_benchmark — end-to-end driver wiring snapshot/mine/
prefetch_bench/conversation_runner together (final-review recommendation).

The CLI's real-ChromaDB read (_load_snapshot_entries) and argparse main() are
untested I/O plumbing (mirrors scripts/snapshot_episodic_for_benchmark.py's
own tested-core/untested-CLI split) — this file covers the two testable
pieces: run_real_data_benchmark's orchestration and _summary_lines'
formatting, both pure aside from the orchestration function's async calls
into fakes.
"""
from __future__ import annotations

import asyncio

import chromadb

from benchmark.prefetch_metrics import PrefetchMetrics
from scripts.run_real_data_benchmark import _load_snapshot_entries, _summary_lines, run_real_data_benchmark


def _hit(message_id: str) -> dict:
    return {"content": message_id, "metadata": {"message_id": message_id}}


class _FakeLayers:
    """Mirrors tests/test_prefetch_bench.py's fake: cold finds the target via bm25."""

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


class _FakeRunner:
    """Fake BenchmarkRunner: records which cases run_conversation was called with."""

    def __init__(self, conversations_by_case_id: dict[str, dict]) -> None:
        self._conversations = conversations_by_case_id
        self.calls: list[str] = []

    async def run_conversation(self, case: dict) -> dict:
        self.calls.append(case["id"])
        return self._conversations[case["id"]]


def _conversation(grounded_warm: bool, grounded_cold: bool, warm_served: bool,
                   hallucinated_warm: list[str] | None = None) -> dict:
    return {
        "warm": {
            "response": "r", "chat_id": "c1", "warm_served": warm_served,
            "groundedness": {"grounded": grounded_warm, "hallucinated_claims": hallucinated_warm or [],
                              "answered_without_evidence": False},
        },
        "cold": {
            "response": "r", "chat_id": "c2", "warm_served": False,
            "groundedness": {"grounded": grounded_cold, "hallucinated_claims": [],
                              "answered_without_evidence": False},
        },
    }


def test_run_real_data_benchmark_combines_prefetch_and_conversation_results():
    layers = _FakeLayers()
    cases = [
        {"id": "c1", "query": "q1", "message_id": "target", "chat_id_source": "src"},
        {"id": "c2", "query": "q2", "message_id": "missing", "chat_id_source": "src"},
    ]
    runner = _FakeRunner({
        "c1": _conversation(grounded_warm=True, grounded_cold=False, warm_served=True),
        "c2": _conversation(grounded_warm=False, grounded_cold=True, warm_served=False),
    })

    result = asyncio.run(run_real_data_benchmark(cases, layers, runner))

    assert isinstance(result["prefetch_metrics"], PrefetchMetrics)
    assert result["prefetch_metrics"].total_cases == 2
    assert result["prefetch_metrics"].hit_rate_by_state["cold"] == 0.5
    assert runner.calls == ["c1", "c2"]
    assert len(result["conversations"]) == 2
    assert result["conversations"][0]["warm"]["groundedness"]["grounded"] is True


def test_summary_lines_reports_prefetch_and_conversation_stats():
    pm = PrefetchMetrics(
        total_cases=2,
        hit_rate_by_state={"cold": 0.5, "warm": 1.0, "drift": 0.5},
        mean_rank_by_state={"cold": 1.0, "warm": 1.5, "drift": None},
        mechanism_hit_counts_by_state={"cold": {"bm25": 1}, "warm": {"prediction": 2}, "drift": {}},
    )
    conversations = [
        _conversation(grounded_warm=True, grounded_cold=False, warm_served=True,
                      hallucinated_warm=["a claim"]),
        _conversation(grounded_warm=False, grounded_cold=True, warm_served=False),
    ]
    lines = _summary_lines({"prefetch_metrics": pm, "conversations": conversations})
    text = "\n".join(lines)

    assert "Cases: 2" in text
    assert "cold: hit@K=50.0%" in text
    assert "warm_served: 1/2" in text
    assert "grounded (warm): 1/2" in text
    assert "grounded (cold): 1/2" in text
    assert "hallucinated claims (warm): 1   (cold): 0" in text


class _FakeChromaCollection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def get(self, include=None):
        return {
            "ids": [r["id"] for r in self._rows],
            "documents": [r["content"] for r in self._rows],
            "metadatas": [r["metadata"] for r in self._rows],
        }


class _FakeChromaClient:
    def __init__(self, col: _FakeChromaCollection) -> None:
        self._col = col

    def get_or_create_collection(self, name):
        return self._col


def test_load_snapshot_entries_reads_verbatim_rows(monkeypatch):
    rows = [{"id": "a", "content": "hello", "metadata": {"chat_id": "c1"}}]
    col = _FakeChromaCollection(rows)
    monkeypatch.setattr(chromadb, "PersistentClient", lambda path, settings=None: _FakeChromaClient(col))

    entries = _load_snapshot_entries("chroma_data_benchmark", "episodic_memory")
    assert entries == rows


def test_summary_lines_handles_empty_conversations():
    pm = PrefetchMetrics(
        total_cases=0, hit_rate_by_state={"cold": 0.0, "warm": 0.0, "drift": 0.0},
        mean_rank_by_state={"cold": None, "warm": None, "drift": None},
        mechanism_hit_counts_by_state={"cold": {}, "warm": {}, "drift": {}},
    )
    lines = _summary_lines({"prefetch_metrics": pm, "conversations": []})
    text = "\n".join(lines)
    assert "warm_served: 0/0" in text
