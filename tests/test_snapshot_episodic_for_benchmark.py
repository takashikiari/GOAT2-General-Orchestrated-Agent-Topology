"""tests.test_snapshot_episodic_for_benchmark — read-only live->benchmark export (spec §4.1).

Fakes both the source and destination ChromaDB collections/client so no real
ChromaDB is touched — mirrors the row-count safety check already proven out
in scripts/repair_episodic.py.
"""
from __future__ import annotations

import asyncio

from scripts.snapshot_episodic_for_benchmark import export_snapshot


class _FakeSourceCollection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def get(self, include=None):
        return {
            "ids": [r["id"] for r in self._rows],
            "documents": [r["content"] for r in self._rows],
            "metadatas": [r["metadata"] for r in self._rows],
        }

    def count(self) -> int:
        return len(self._rows)


class _FakeDestCollection:
    def __init__(self) -> None:
        self.added = {"ids": [], "documents": [], "metadatas": []}

    def add(self, ids, documents, metadatas):
        self.added["ids"].extend(ids)
        self.added["documents"].extend(documents)
        self.added["metadatas"].extend(metadatas)

    def count(self) -> int:
        return len(self.added["ids"])


class _FakeDestClient:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeDestCollection] = {}
        self.deleted: list[str] = []

    def delete_collection(self, name):
        self.deleted.append(name)
        self.collections.pop(name, None)

    def get_or_create_collection(self, name):
        if name not in self.collections:
            self.collections[name] = _FakeDestCollection()
        return self.collections[name]


def test_export_snapshot_copies_rows_verbatim():
    rows = [
        {"id": "a", "content": "hello", "metadata": {"chat_id": "c1"}},
        {"id": "b", "content": "world", "metadata": {"chat_id": "c2"}},
    ]
    source = _FakeSourceCollection(rows)
    dest_client = _FakeDestClient()
    count = asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    assert count == 2
    dest_col = dest_client.collections["bench_col"]
    assert dest_col.added["ids"] == ["a", "b"]
    assert dest_col.added["documents"] == ["hello", "world"]
    assert dest_col.added["metadatas"] == [{"chat_id": "c1"}, {"chat_id": "c2"}]


def test_export_snapshot_aborts_on_row_count_mismatch():
    rows = [{"id": "a", "content": "x", "metadata": {}}]
    source = _FakeSourceCollection(rows)
    source.count = lambda: 5  # simulate a desync between get() and count()
    dest_client = _FakeDestClient()
    raised = False
    try:
        asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    except RuntimeError as exc:
        raised = True
        assert "aborting" in str(exc)
    assert raised, "expected RuntimeError on row-count mismatch"
    assert "bench_col" not in dest_client.collections  # no write happened


def test_export_snapshot_is_idempotent_drop_and_recreate():
    rows = [{"id": "a", "content": "x", "metadata": {}}]
    source = _FakeSourceCollection(rows)
    dest_client = _FakeDestClient()
    asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    assert dest_client.deleted == ["bench_col", "bench_col"]
    assert dest_client.collections["bench_col"].count() == 1  # not doubled
