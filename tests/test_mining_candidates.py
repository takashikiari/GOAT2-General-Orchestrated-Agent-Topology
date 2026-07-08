"""tests.test_mining_candidates — information-dense entry selection (spec §4.2).

Also enforces that a candidate's stored metadata carries message_id: without
it, real_data_mining.generate_case's ground-truth message_id falls back to
the ChromaDB row id, but EpisodicMemory.search() returns metadata verbatim
(no retroactive fallback) — so that ground truth can never match a retrieved
result for entries written before the message_id field existed. Confirmed on
real data during the first end-to-end benchmark run (2026-07-08): a mined
entry's stored metadata was {"timestamp":..., "chat_id":..., "tags":...} with
no message_id key at all, producing an unfalsifiable hit@K=0% for prefetch_bench.
"""
from __future__ import annotations

from benchmark.mining_candidates import select_candidates


def _entry(content: str, importance: float | None = None, message_id: str | None = "msg-x") -> dict:
    metadata: dict = {}
    if importance is not None:
        metadata["importance"] = importance
    if message_id is not None:
        metadata["message_id"] = message_id
    return {"id": "x", "content": content, "metadata": metadata}


def test_excludes_short_chit_chat():
    entries = [_entry("GOAT"), _entry("Ce faci nebunule?")]
    assert select_candidates(entries) == []


def test_includes_long_content_with_no_importance_metadata():
    long_text = " ".join(["word"] * 20)
    entries = [_entry(long_text)]
    assert select_candidates(entries) == entries


def test_excludes_long_content_with_low_importance():
    long_text = " ".join(["word"] * 20)
    entries = [_entry(long_text, importance=0.1)]
    assert select_candidates(entries) == []


def test_includes_long_content_with_high_importance():
    long_text = " ".join(["word"] * 20)
    entries = [_entry(long_text, importance=0.8)]
    assert select_candidates(entries) == entries


def test_excludes_entry_missing_message_id():
    """Real data (2026-07-08 run): entries predating the message_id field must
    be excluded — their mined ground truth could never match on retrieval."""
    long_text = " ".join(["word"] * 20)
    entries = [_entry(long_text, message_id=None)]
    assert select_candidates(entries) == []


def test_excludes_entry_with_empty_message_id():
    long_text = " ".join(["word"] * 20)
    entry = _entry(long_text)
    entry["metadata"]["message_id"] = ""
    assert select_candidates([entry]) == []
