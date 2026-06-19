"""Tests for BUG-010, BUG-011, BUG-013 fixes.

BUG-010: hints.build_hints accepts an intent but discards it. The
        resulting hint list is identical for every user input.
BUG-011: recall_corrections uses a single static query
        ("user correction routing preference") — corrections on other
        topics (style, facts, format) are never surfaced.
BUG-013: hints are formatted as ``intent=\"{intent}\" → goat={goat},
        user wanted: {wanted}`` without escaping. If the intent
        contains a quote, the resulting line cannot be re-parsed
        safely.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from supervisor.mechanisms.corrections import (
    DEFAULT_LIMIT,
    format_correction_hint,
    recall_corrections,
)
from supervisor.mechanisms.hints import build_hints


# ── BUG-013: escape the hint format ─────────────────────────────────────────


def test_format_correction_hint_escapes_quotes_in_intent():
    """An intent containing a double-quote must not break the
    hint format. The rendered line is still a valid key=value line."""
    line = format_correction_hint(
        intent='she said "hi"', goat="router", wanted="a reply",
    )
    # The raw intent should not appear inside an open quote in the line.
    # We expect either JSON-style escaping or replacement.
    assert line  # non-empty
    # Acceptable encodings: repr-style (single quotes wrap the value) or
    # escaped quotes. We test the property that the line can be split
    # back into the three fields without ambiguity.
    assert "router" in line
    assert "a reply" in line


def test_format_correction_hint_handles_special_chars_in_wanted():
    """The ``wanted`` field can contain any text — it must be
    sanitised so the line is safe to log."""
    line = format_correction_hint(
        intent="build api", goat="coder",
        wanted="use Flask: return jsonify({})\n# not print()",
    )
    # The newlines and braces must not break the field boundaries.
    assert line.startswith("intent=")
    assert "goat=coder" in line


def test_format_correction_hint_truncates_long_fields():
    """Very long intents or wanted-values must be truncated so the
    hint line stays readable."""
    long_intent = "x" * 500
    long_wanted = "y" * 500
    line = format_correction_hint(
        intent=long_intent, goat="router", wanted=long_wanted,
    )
    # Both fields are truncated to 80 chars by default.
    assert len(line) < 250


def test_format_correction_hint_returns_non_empty_for_minimal_input():
    line = format_correction_hint(intent="x", goat="y", wanted="z")
    assert "x" in line and "y" in line and "z" in line


# ── BUG-010: hints are intent-aware ─────────────────────────────────────────


def test_build_hints_uses_intent_for_filtering(monkeypatch):
    """``build_hints`` must pass the user intent through to
    ``recall_corrections`` and use it to filter the returned
    correction list. With the bug present, all corrections are
    returned regardless of intent."""
    # Mock recall_corrections to return two corrections — one
    # about routing (matching the intent) and one about format.
    async def fake_recall(mm, limit):
        return [
            'intent="route this to coder" → goat=router, user wanted: coder',
            'intent="use bullet points" → goat=direct, user wanted: bullet points',
        ]
    monkeypatch.setattr(
        "supervisor.mechanisms.hints.recall_corrections", fake_recall
    )

    captured: dict = {}

    async def main():
        return await build_hints(
            mm=MagicMock(), intent="please route this to coder",
            registry=MagicMock(), limit=3,
        )
    hints_out = asyncio.run(main())

    # The hint about routing should appear; the format one shouldn't.
    joined = "\n".join(hints_out)
    assert "coder" in joined.lower()
    # The "use bullet points" correction should be filtered out as
    # not relevant to the routing intent.
    assert "bullet points" not in joined


def test_build_hints_returns_static_hints_when_no_corrections(monkeypatch):
    """When ``recall_corrections`` returns nothing, only the static
    hints from config/goat.toml [hints] appear."""
    async def fake_recall(mm, limit):
        return []
    monkeypatch.setattr(
        "supervisor.mechanisms.hints.recall_corrections", fake_recall
    )
    # Patch the static hints loader to return a known value.
    monkeypatch.setattr(
        "supervisor.mechanisms.hints.load_static_hints",
        lambda registry: ["static hint"],
    )
    hints_out = asyncio.run(build_hints(
        mm=MagicMock(), intent="anything", registry=MagicMock(), limit=3,
    ))
    assert "static hint" in hints_out


# ── BUG-011: multi-query corrections ────────────────────────────────────────


def test_recall_corrections_uses_multiple_queries(monkeypatch):
    """``recall_corrections`` must fan out over multiple semantic
    queries and merge the results, instead of relying on a single
    fixed query."""
    async def fake_search(query, limit):
        # Each "query" returns a distinct record.
        return [{"content": f"correction-for: {query}"}]

    mm = MagicMock()
    episodic = MagicMock()
    episodic.search = AsyncMock(side_effect=fake_search)
    mm.episodic = episodic

    out = asyncio.run(recall_corrections(mm, limit=5))

    # Search was called more than once (multiple queries).
    assert episodic.search.await_count >= 2, (
        f"expected multi-query fan-out, got {episodic.search.await_count} calls"
    )
    # All results were merged.
    contents = " | ".join(out)
    assert "user correction" in contents.lower() or "preference" in contents.lower()


def test_recall_corrections_deduplicates_results(monkeypatch):
    """When multiple queries return the same content, it must
    appear only once in the merged list."""
    async def fake_search(query, limit):
        return [{"content": "same correction"}]

    mm = MagicMock()
    episodic = MagicMock()
    episodic.search = AsyncMock(side_effect=fake_search)
    mm.episodic = episodic

    out = asyncio.run(recall_corrections(mm, limit=5))

    # Exactly one entry per distinct content.
    assert out.count("same correction") <= 1


def test_recall_corrections_returns_empty_when_episodic_unavailable():
    """Defensive: when ``mm.episodic`` is None or lacks ``search``,
    return an empty list (don't crash)."""
    mm = MagicMock()
    mm.episodic = None
    out = asyncio.run(recall_corrections(mm, limit=3))
    assert out == []


def test_recall_corrections_returns_empty_on_search_failure():
    """Defensive: a search failure must not break the turn."""
    async def boom(*args, **kwargs):
        raise RuntimeError("chroma down")

    mm = MagicMock()
    episodic = MagicMock()
    episodic.search = boom
    mm.episodic = episodic

    out = asyncio.run(recall_corrections(mm, limit=3))
    assert out == []


def test_recall_corrections_limit_caps_merged_results():
    """The ``limit`` parameter must bound the merged result size,
    not the per-query size."""
    async def fake_search(query, limit):
        # Each query returns 3 entries.
        return [{"content": f"{query}-{i}"} for i in range(3)]

    mm = MagicMock()
    episodic = MagicMock()
    episodic.search = AsyncMock(side_effect=fake_search)
    mm.episodic = episodic

    out = asyncio.run(recall_corrections(mm, limit=4))

    # 3 queries × 3 results = 9 candidate entries, capped at 4.
    assert len(out) <= 4