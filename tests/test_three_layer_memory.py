"""Tests for 3-layer memory structure (Faza 2 Commit 1).

The mem_inject layer now assembles 3 explicit temporal blocks:

  [Present]          — working memory, age < present_max_age_s
  [Present-Past]     — working memory, fresh_max ≤ age < present_past_max_age_s
                       + episodic recall (top-K by relevance)
  [Past]             — working memory, age ≥ present_past_max_age_s
                       + Letta persona block (user identity, prefs)

Each layer is rendered as a labelled block with its own entry
cap. The structure is config-driven via [temporal_layers] in
config/memory.toml.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from supervisor.session import mem_inject
from supervisor.session.episodic_cache import (
    EpisodicRecallCache,
    set_episodic_cache,
)
from supervisor.session.mem_inject import (
    LAYER_PRESENT,
    LAYER_PRESENT_PAST,
    LAYER_PAST,
    _present_max_age_s,
    _present_past_max_age_s,
    _present_max_entries,
    _present_past_max_entries,
    _past_max_entries,
    mem_turn,
)


@pytest.fixture(autouse=True)
def _fresh_episodic_cache():
    """Reset the episodic cache singleton for every test.

    Without this, the first test that exercises ``mem_turn`` with
    a default (empty) ``episodic_hits`` mock caches ``[]`` for the
    (intent, SESSION_ROLE, top_k, 0) key, and later tests that
    supply real hits get the stale cached ``[]`` back.
    """
    set_episodic_cache(EpisodicRecallCache())
    yield
    set_episodic_cache(None)


# ── Helpers ────────────────────────────────────────────────────────────────


def _working_record(key: str, content: str, ts: float) -> SimpleNamespace:
    """Build a working-memory record (MemoryEntry shape) for tests."""
    return SimpleNamespace(
        key=key,
        content=content,
        metadata={"created_at_ts": ts},
        source="working",
    )


def _episodic_hit(content: str) -> SimpleNamespace:
    """Build an episodic recall hit for tests."""
    return SimpleNamespace(content=content)


def _memory_manager(
    *,
    working_records: list | None = None,
    episodic_hits: list | None = None,
    persona_block: str = "",
) -> MagicMock:
    """Build a MagicMock MemoryManager with the call sites that
    mem_turn uses: working.backend (list, get), recall (async),
    long_term.get_block (async)."""
    mm = MagicMock()
    mm.working = MagicMock()
    mm.working.backend = MagicMock()
    mm.working.backend.keys = AsyncMock(return_value=[r.key for r in (working_records or [])])

    async def _get(role, k):
        for r in (working_records or []):
            if r.key == k:
                return r
        return None
    mm.working.backend.get = AsyncMock(side_effect=_get)

    mm.recall = AsyncMock(return_value=episodic_hits or [])
    mm.long_term = MagicMock()
    mm.long_term.get_block = AsyncMock(return_value=persona_block)
    return mm


# ── Config-driven defaults ────────────────────────────────────────────────


def test_config_defaults_define_three_layers():
    """The three layers must be defined in the config with
    sensible defaults. The config file [temporal_layers] is the
    single source of truth."""
    # Threshold constants are loaded from config at import time.
    # Verify the canonical values match the documented policy.
    assert _present_max_age_s == 300          # 5 min
    assert _present_past_max_age_s == 86400   # 24h
    assert _present_max_entries == 50
    assert _present_past_max_entries == 30
    assert _past_max_entries == 20


def test_layer_label_constants_exported():
    """The three layer labels are exported so other modules
    (memory_advisor, MCP tools) can reference them."""
    for label in (LAYER_PRESENT, LAYER_PRESENT_PAST, LAYER_PAST):
        assert isinstance(label, str)
        assert label  # non-empty


# ── Present layer: fresh working memory ──────────────────────────────────


def test_present_layer_only_contains_fresh_entries():
    """Entries fresher than present_max_age_s go to [Present].
    Older entries are routed to [Present-Past] or [Past]."""
    now = time.time()
    mm = _memory_manager(working_records=[
        _working_record("turn:5:intent", "just now", now - 5),
        _working_record("turn:4:intent", "1m ago", now - 60),
        _working_record("turn:3:intent", "4m ago", now - 240),
    ])
    # All three are < 300s old → all in [Present].
    result = asyncio_run(mem_turn(mm, "intent"))
    assert "[Present]" in result
    assert "just now" in result
    assert "1m ago" in result
    assert "4m ago" in result
    # [Present-Past] and [Past] headers are always present,
    # but the bodies must be empty (no entries routed there).
    pp_section = result.split("[Present-Past]")[1].split("[Past]")[0] if "[Present-Past]" in result else ""
    past_section = result.split("[Past]")[1] if "[Past]" in result else ""
    # Allow the persona line in [Past] but no working-memory lines.
    pp_lines = [l for l in pp_section.split("\n") if l.startswith("- ")]
    past_working_lines = [
        l for l in past_section.split("\n")
        if l.startswith("- ") and "[working]" in l
    ]
    assert pp_lines == [], f"Present-Past must be empty, got: {pp_lines}"
    assert past_working_lines == [], (
        f"Past working entries must be empty, got: {past_working_lines}"
    )


# ── Present-Past layer: recent + episodic recall ─────────────────────────


def test_recent_entries_routed_to_present_past():
    """Entries between present_max_age_s and present_past_max_age_s
    go to [Present-Past], not [Present]."""
    now = time.time()
    mm = _memory_manager(working_records=[
        _working_record("turn:5:intent", "2h ago", now - 7200),
        _working_record("turn:4:intent", "6h ago", now - 21600),
    ])
    result = asyncio_run(mem_turn(mm, "intent"))
    assert "[Present-Past]" in result
    # [Present] header is always present; the body must be empty
    # because no entry is < 300s old.
    present_section = result.split("[Present]")[1].split("[Present-Past]")[0]
    present_lines = [l for l in present_section.split("\n") if l.startswith("- ")]
    assert present_lines == [], f"Present body must be empty, got: {present_lines}"
    assert "[Past]" in result
    # Past working entries must be empty too (2h and 6h are < 24h).
    past_section = result.split("[Past]")[1]
    past_working_lines = [
        l for l in past_section.split("\n")
        if l.startswith("- ") and "[working]" in l
    ]
    assert past_working_lines == [], (
        f"Past working must be empty, got: {past_working_lines}"
    )
    assert "2h ago" in result
    assert "6h ago" in result


def test_present_past_includes_episodic_recall():
    """[Present-Past] includes top-K episodic recall hits."""
    now = time.time()
    mm = _memory_manager(
        working_records=[
            _working_record("turn:5:intent", "2h ago", now - 7200),
        ],
        episodic_hits=[
            _episodic_hit("episodic: project GOAT needs memory advisor"),
            _episodic_hit("episodic: previous debug session was about X"),
        ],
    )
    result = asyncio_run(mem_turn(mm, "intent"))
    # Both working recent AND episodic recall appear under Present-Past.
    assert "2h ago" in result
    assert "project GOAT needs memory advisor" in result
    assert "previous debug session was about X" in result
    # Episodic hits are labelled with [episodic] prefix.
    assert "[episodic]" in result


def test_present_past_caps_episodic_top_k():
    """[Present-Past] limits episodic recall to config.episodic_top_k."""
    now = time.time()
    hits = [_episodic_hit(f"unique-string-{i:02d}") for i in range(20)]
    mm = _memory_manager(
        working_records=[
            _working_record("turn:5:intent", "2h ago", now - 7200),
        ],
        episodic_hits=hits,
    )
    result = asyncio_run(mem_turn(mm, "intent"))
    # Default episodic_top_k is 5. Verify only 5 hits appear.
    for i in range(5):
        assert f"unique-string-{i:02d}" in result
    # And hits 5..19 do NOT appear. We use the [episodic] prefix
    # so the substring match is unambiguous.
    for i in range(5, 20):
        assert f"[episodic] unique-string-{i:02d}" not in result


# ── Past layer: old working memory + Letta persona ─────────────────────


def test_old_entries_routed_to_past():
    """Entries older than present_past_max_age_s go to [Past]."""
    now = time.time()
    mm = _memory_manager(working_records=[
        _working_record("turn:3:intent", "2d ago", now - 172800),
    ])
    result = asyncio_run(mem_turn(mm, "intent"))
    assert "[Past]" in result
    assert "2d ago" in result


def test_past_includes_letta_persona():
    """[Past] includes the Letta persona block (user identity,
    long-term preferences) when available."""
    now = time.time()
    mm = _memory_manager(
        working_records=[],
        persona_block="formality: casual\ntone: friendly\nlanguage: Romanian",
    )
    result = asyncio_run(mem_turn(mm, "intent"))
    assert "[Past]" in result
    assert "persona:" in result
    assert "formality: casual" in result


def test_past_layer_unavailable_when_letta_down():
    """When Letta is unreachable, the [Past] block renders a
    clear 'unavailable' marker instead of crashing or hiding
    the layer entirely."""
    now = time.time()
    mm = MagicMock()
    mm.working = MagicMock()
    mm.working.backend = MagicMock()
    mm.working.backend.keys = AsyncMock(return_value=[])
    mm.working.backend.get = AsyncMock(return_value=None)
    mm.recall = AsyncMock(return_value=[])
    mm.long_term = MagicMock()
    mm.long_term.get_block = AsyncMock(side_effect=RuntimeError("letta down"))

    result = asyncio_run(mem_turn(mm, "intent"))
    # The Past block must be present (so the LLM sees the layer
    # exists) but the Letta content is replaced with an
    # unavailable marker.
    assert "[Past]" in result
    assert "unavailable" in result.lower()


# ── Entry caps per layer ─────────────────────────────────────────────────


def test_present_layer_caps_at_max_entries():
    """[Present] must not exceed present_max_entries."""
    now = time.time()
    # 60 fresh entries; cap is 50.
    records = [
        _working_record(f"turn:{i}:intent", f"msg-{i}", now - i)
        for i in range(60)
    ]
    mm = _memory_manager(working_records=records)
    result = asyncio_run(mem_turn(mm, "intent"))
    # Count entries under [Present] block.
    present_section = result.split("[Present-Past]")[0] if "[Present-Past]" in result else result
    present_lines = [
        line for line in present_section.split("\n")
        if line.startswith("- ")
    ]
    assert len(present_lines) <= _present_max_entries


def test_present_past_layer_caps_at_max_entries():
    """[Present-Past] must not exceed present_past_max_entries,
    summed across working recent + episodic hits."""
    now = time.time()
    # 40 working recent + 20 episodic hits. Cap is 30.
    records = [
        _working_record(f"turn:{i}:intent", f"wm-{i}", now - 3600 - i)
        for i in range(40)
    ]
    hits = [_episodic_hit(f"ep-{i}") for i in range(20)]
    mm = _memory_manager(working_records=records, episodic_hits=hits)
    result = asyncio_run(mem_turn(mm, "intent"))
    # Extract the Present-Past section (between headers).
    sections = result.split("[")
    # Section 2 is Present-Past (after [Present]).
    pp_section = "[".join(sections[2:3]) if len(sections) > 2 else ""
    # If Past also present, take up to next section.
    if "[Past]" in pp_section:
        pp_section = pp_section.split("[Past]")[0]
    pp_lines = [
        line for line in pp_section.split("\n")
        if line.startswith("- ")
    ]
    assert len(pp_lines) <= _present_past_max_entries


# ── Label headers in output ──────────────────────────────────────────────


def test_output_has_three_section_headers():
    """The output must contain all three section headers —
    [Present], [Present-Past], [Past] — even when some are empty."""
    mm = _memory_manager(working_records=[])
    result = asyncio_run(mem_turn(mm, "intent"))
    assert "[Present]" in result
    assert "[Present-Past]" in result
    assert "[Past]" in result


def test_empty_layers_still_render_headers():
    """An empty layer still shows the header so the LLM knows
    the layer exists and would be populated in non-empty sessions."""
    now = time.time()
    mm = _memory_manager(working_records=[])
    result = asyncio_run(mem_turn(mm, "intent"))
    # All three headers present.
    for header in ("[Present]", "[Present-Past]", "[Past]"):
        assert header in result, f"missing header {header}"


# ── Backward compatibility ───────────────────────────────────────────────


def test_mm_none_returns_unavailable_marker():
    """When MemoryManager is None, mem_turn returns the same
    UNAVAILABLE marker as before — backward compatible."""
    result = asyncio_run(mem_turn(None, "intent"))
    assert "UNAVAILABLE" in result.upper()


def test_recall_failure_doesnt_break_present_past():
    """If episodic recall raises, the Present-Past layer
    renders with working memory only (no episodic entries) —
    the rest of the structure is preserved."""
    now = time.time()
    mm = MagicMock()
    mm.working = MagicMock()
    mm.working.backend = MagicMock()
    mm.working.backend.keys = AsyncMock(return_value=["turn:5:intent"])
    async def _get(role, k):
        return _working_record("turn:5:intent", "2h ago", now - 7200)
    mm.working.backend.get = AsyncMock(side_effect=_get)
    mm.recall = AsyncMock(side_effect=RuntimeError("chroma down"))
    mm.long_term = MagicMock()
    mm.long_term.get_block = AsyncMock(return_value="")

    result = asyncio_run(mem_turn(mm, "intent"))
    # Structure preserved.
    assert "[Present]" in result
    assert "[Present-Past]" in result or "[Past]" in result
    # And the present-past working entry survived.
    assert "2h ago" in result


# ── asyncio.run helper (pytest-asyncio mode is 'auto') ──────────────────


def asyncio_run(coro):
    """Run an awaitable in a new event loop (pytest-asyncio
    'auto' mode handles this, but we wrap defensively in case
    the test is run outside a loop)."""
    import asyncio
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Already in a running loop — use the running loop.
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)