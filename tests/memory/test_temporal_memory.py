"""Temporal memory tests: parser, filter, timeline, recent, debug_trace."""
from __future__ import annotations

import asyncio
import time

import pytest

from memory.temporal_filter import filter_by_time, resolve_range
from memory.time_parser import parse_time_range
from memory.shared.types import AgentRole, EntryId, MemoryEntry, MemoryEntryMetadata, MemoryKey

_UTC_EPOCH = __import__("datetime").timezone.utc
_R = AgentRole("ttest")


def _e(key: str, ts: float | None) -> MemoryEntry:
    meta = MemoryEntryMetadata(tags=[])
    if ts is not None:
        meta["created_at_ts"] = ts
    iso = __import__("datetime").datetime.fromtimestamp(ts, _UTC_EPOCH).isoformat() if ts else ""
    return MemoryEntry(id=EntryId(key), agent_role=_R, key=MemoryKey(key),
                       content=f"content-{key}", metadata=meta, created_at=iso, source="working")


# --- time_parser ---

def test_parse_yesterday_morning():
    s, e = parse_time_range("yesterday morning")
    assert s is not None and e is not None and e > s
    assert 5.5 * 3600 <= (e - s) <= 6.5 * 3600


def test_parse_last_24h():
    now = time.time()
    s, e = parse_time_range("last 24h")
    assert s and e and abs(e - now) < 5 and abs(e - s - 86400) < 5


def test_parse_last_7_days():
    now = time.time()
    s, e = parse_time_range("last 7 days")
    assert s and e and abs(e - s - 7 * 86400) < 5


def test_parse_iso_point():
    s, e = parse_time_range("2026-01-01T10:00:00")
    assert s is not None and e is None


def test_parse_unknown_returns_none():
    assert parse_time_range("totally random phrase xyz") == (None, None)


def test_parse_empty_string():
    assert parse_time_range("") == (None, None)


# --- filter_by_time ---

def test_filter_no_range_returns_all():
    entries = [_e("a", 1000.0), _e("b", 2000.0)]
    assert filter_by_time(entries, None, None) == entries


def test_filter_range_keeps_in_range():
    now = time.time()
    entries = [_e("old", now - 10000), _e("recent", now - 100)]
    result = filter_by_time(entries, now - 500, now)
    assert len(result) == 1 and result[0].key == "recent"


def test_filter_excludes_out_of_range():
    now = time.time()
    entries = [_e("a", now - 1000), _e("b", now - 100)]
    result = filter_by_time(entries, now - 200, now)
    assert all(e.key == "b" for e in result)


def test_filter_no_timestamp_excluded_when_filter_active():
    entries = [_e("nots", None), _e("has_ts", 1000.0)]
    result = filter_by_time(entries, 500.0, 2000.0)
    assert len(result) == 1 and result[0].key == "has_ts"


def test_filter_no_timestamp_included_when_no_filter():
    entries = [_e("nots", None)]
    assert filter_by_time(entries, None, None) == entries


# --- resolve_range ---

def test_resolve_compound_expression():
    s, e = resolve_range("yesterday morning", None)
    assert s is not None and e is not None and e > s


def test_resolve_explicit_bounds():
    s, e = resolve_range("2026-01-01T00:00:00", "2026-01-02T00:00:00")
    assert s is not None and e is not None and e > s


# --- working memory integration (no external deps) ---

def test_working_store_search_no_filter():
    from memory.working.working_memory import WorkingMemoryLayer

    async def _run():
        wm = WorkingMemoryLayer()
        await wm.store(_R, MemoryKey("hello"), "unique world content")
        results = await wm.search(_R, "world")
        assert any(e.key == "hello" for e in results)

    asyncio.run(_run())


def test_working_list_returns_recent_first():
    from memory.working.working_memory import WorkingMemoryLayer

    async def _run():
        base = time.time()
        wm = WorkingMemoryLayer()
        for i in range(3):
            # Pass created_at_ts explicitly so the sort order is deterministic
            await wm.store(_R, MemoryKey(f"r{i}"), f"data {i}",
                           metadata=MemoryEntryMetadata(tags=[], created_at_ts=base + i))
        entries = await wm.list(_R, limit=10)
        assert entries and entries[0].key == "r2"

    asyncio.run(_run())


def test_no_timestamp_no_crash():
    from memory.working.working_memory import WorkingMemoryLayer

    async def _run():
        wm = WorkingMemoryLayer()
        await wm.store(_R, MemoryKey("nots"), "no explicit ts")
        entries = await wm.list(_R, limit=10)
        result = filter_by_time(entries, time.time() - 100, time.time() + 100)
        assert isinstance(result, list)

    asyncio.run(_run())


def test_debug_trace_structure():
    from memory.shared.memory_enums import MemoryType
    from memory.temporal_search import TemporalSearchMixin
    from memory.working.working_memory import WorkingMemoryLayer

    class _MM(TemporalSearchMixin):
        def __init__(self) -> None:
            wm = WorkingMemoryLayer()
            self._wm = wm
            self._layers = {MemoryType(t): wm for t in ("working", "episodic", "long_term")}

    async def _run():
        mm = _MM()
        await mm._wm.store(_R, MemoryKey("trace"), "trace content for testing")
        result = await mm.debug_trace(str(_R), "trace")
        assert "tiers" in result and "query" in result
        assert set(result["tiers"]) == {"working", "episodic", "long_term"}
        assert result["tiers"]["working"]["total"] >= 1

    asyncio.run(_run())


def test_timeline_empty_range():
    from memory.shared.memory_enums import MemoryType
    from memory.temporal_search import TemporalSearchMixin
    from memory.working.working_memory import WorkingMemoryLayer

    class _MM(TemporalSearchMixin):
        def __init__(self) -> None:
            wm = WorkingMemoryLayer()
            self._layers = {MemoryType(t): wm for t in ("working", "episodic", "long_term")}

    async def _run():
        mm = _MM()
        result = await mm.timeline(str(_R), "2030-01-01", "2030-01-02", limit=10)
        assert result == []

    asyncio.run(_run())
