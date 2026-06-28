"""tests.test_episodic_queries — bulk read/delete via the EpisodicQueries mixin.

Uses a fake ChromaDB collection (no ChromaDB install needed) so the split
(``episodic.py`` core + ``queries.py`` mixin) is exercised through the real
``EpisodicMemory`` public methods. Mirrors the suite's ``asyncio.run`` convention.
"""
from __future__ import annotations

import asyncio

from memory.episodic import EpisodicMemory


class _FakeCollection:
    """Minimal sync ChromaDB-like surface for get/count/delete."""

    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def get(self, where: dict | None = None, include=None):
        ids, docs, metas = [], [], []
        for e in self._entries:
            if where and not all(e["metadata"].get(k) == v for k, v in where.items()):
                continue
            ids.append(e["id"]); docs.append(e["content"]); metas.append(e["metadata"])
        return {"ids": ids, "documents": docs, "metadatas": metas}

    def count(self) -> int:
        return len(self._entries)

    def delete(self, ids: list[str]) -> None:
        self._entries = [e for e in self._entries if e["id"] not in ids]

    def query(self, query_texts=None, n_results=5, where=None):
        # Return closest-first with synthetic distances (lower = closer, L2).
        n = n_results or 5
        docs = [e["content"] for e in self._entries][:n]
        metas = [e["metadata"] for e in self._entries][:n]
        dists = [0.2 * i for i in range(len(docs))]      # 0.0, 0.2, 0.4, ...
        return {"ids": [[e["id"] for e in self._entries[:n]]],
                "documents": [docs], "metadatas": [metas], "distances": [dists]}


def _episodic(entries: list[dict]) -> EpisodicMemory:
    e = EpisodicMemory()
    e._collection = _FakeCollection(entries)  # bypass lazy ChromaDB init
    return e


def _entry(i: int, chat: str, content: str, ts: float) -> dict:
    return {"id": f"id{i}", "content": content, "metadata": {"chat_id": chat, "timestamp": ts}}


def test_count_global_and_filtered():
    e = _episodic([_entry(0, "a", "x", 1), _entry(1, "b", "y", 2), _entry(2, "a", "z", 3)])
    assert asyncio.run(e.count()) == 3
    assert asyncio.run(e.count("a")) == 2
    assert asyncio.run(e.count("b")) == 1


def test_get_recent_chronological_and_filtered():
    e = _episodic([_entry(0, "a", "old", 1.0), _entry(1, "a", "mid", 2.0), _entry(2, "a", "new", 3.0)])
    got = asyncio.run(e.get_recent("a", limit=2))
    assert [g["content"] for g in got] == ["mid", "new"]          # chronological, last 2
    e2 = _episodic([_entry(0, "a", "x", 1), _entry(1, "b", "y", 2)])
    assert asyncio.run(e2.get_recent("b", limit=20)) == [{"content": "y", "metadata": {"chat_id": "b", "timestamp": 2}}]


def test_get_oldest_returns_ids_ascending():
    e = _episodic([_entry(0, "a", "new", 3.0), _entry(1, "a", "old", 1.0), _entry(2, "a", "mid", 2.0)])
    oldest = asyncio.run(e.get_oldest(2))
    assert [o["content"] for o in oldest] == ["old", "mid"]
    assert all("id" in o for o in oldest)                          # ids present for deletion


def test_delete_entries_removes_by_id():
    e = _episodic([_entry(0, "a", "x", 1), _entry(1, "a", "y", 2)])
    asyncio.run(e.delete_entries(["id0"]))
    assert asyncio.run(e.count()) == 1
    asyncio.run(e.delete_entries([]))                              # no-op on empty
    assert asyncio.run(e.count()) == 1


def test_mixin_is_attached_to_episodic():
    """The split preserves the public EpisodicMemory surface."""
    e = EpisodicMemory()
    for m in ("get_recent", "count", "get_oldest", "delete_entries"):
        assert callable(getattr(e, m)), f"missing {m}"
    assert EpisodicMemory.__mro__[1].__name__ == "EpisodicQueries"


def test_search_returns_score_closest_first():
    """search surfaces ChromaDB's distance as score (lower = closer)."""
    e = _episodic([
        _entry(0, "a", "close memory", 1.0),
        _entry(1, "a", "far memory", 2.0),
        _entry(2, "a", "furthest memory", 3.0),
    ])
    res = asyncio.run(e.search("close memory", limit=3))
    assert len(res) == 3
    assert res[0]["content"] == "close memory"
    assert "score" in res[0]
    assert res[0]["score"] == 0.0                       # closest -> lowest distance
    assert res[1]["score"] == 0.2
    assert res[2]["score"] == 0.4
    assert all("metadata" in r for r in res)