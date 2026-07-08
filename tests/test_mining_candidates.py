"""tests.test_mining_candidates — information-dense entry selection (spec §4.2)."""
from __future__ import annotations

from benchmark.mining_candidates import select_candidates


def _entry(content: str, importance: float | None = None) -> dict:
    metadata: dict = {}
    if importance is not None:
        metadata["importance"] = importance
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
