"""Tests for turn_persistence style-learning logic.

Verifies:
- BUG-002: _learn_and_persist loads existing style from Letta before analyzing
          so the new profile merges incrementally (no UnboundLocalError / empty merge).
- BUG-001: _store_turn writes intent and summary as separate keys so the
          analyzer trains on real user input, not on prior GOAT summaries.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from config.roles import SESSION_ROLE

from supervisor.session.turn_persistence import _learn_and_persist, _store_turn


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_entry(key: str, content: str) -> MagicMock:
    """Mock MemoryEntry with key and content."""
    e = MagicMock()
    e.key = key
    e.content = content
    return e


def _make_mm() -> MagicMock:
    """Mock MemoryManager for turn_persistence."""
    mm = MagicMock()
    mm.store = AsyncMock()
    mm.working.list = AsyncMock(return_value=[])
    mm.get_block = AsyncMock(return_value="")
    mm.set_block = AsyncMock(return_value=True)
    return mm


# ── BUG-002 tests ───────────────────────────────────────────────────────────


def test_learn_and_persist_loads_existing_style_before_analyze():
    """Regression test: _learn_and_persist must read existing style from
    Letta via load_style BEFORE calling analyze_style, so the new profile
    merges over the old one instead of being a fresh standalone profile.

    Before the fix: `existing` was an undefined local; analyze_style fell
    back to "" and the persona block was overwritten each turn.
    """
    mm = _make_mm()
    mm.working.list = AsyncMock(return_value=[
        _make_entry("turn:5:intent", "ok"),
        _make_entry("turn:5:summary", "Baaa Generale!"),
        _make_entry("turn:4:intent", "hi there"),
    ])
    supervisor = MagicMock()
    supervisor.memory_manager = mm

    existing_text = "formality: casual\ntone: friendly"

    with patch(
        "supervisor.behavior.store.load_style",
        AsyncMock(return_value=existing_text),
    ), patch(
        "supervisor.behavior.style_learner.analyze_style",
        AsyncMock(return_value="formality: casual\ntone: friendly\nlength: terse"),
    ) as analyze_mock, patch(
        "supervisor.behavior.store.save_style",
        AsyncMock(return_value=True),
    ):
        result = asyncio.run(_learn_and_persist(supervisor, mm))

    assert analyze_mock.called, "analyze_style should have been invoked"
    # The 'existing' arg must equal the load_style return value, NOT "".
    call_kwargs = analyze_mock.call_args
    existing_passed = call_kwargs.kwargs.get("existing") or call_kwargs.args[1]
    assert existing_passed == existing_text, (
        f"expected existing='{existing_text}', got '{existing_passed}'"
    )
    assert result is True


def test_learn_and_persist_skips_when_no_intent_entries():
    """When there are no intent-tagged entries (all legacy turn:N), return False
    instead of falling through with summary noise.
    """
    mm = _make_mm()
    mm.working.list = AsyncMock(return_value=[
        _make_entry("turn:5", "turn=5\nintent=ok\nsummary=Baaa"),
    ])
    supervisor = MagicMock()
    supervisor.memory_manager = mm

    with patch(
        "supervisor.behavior.style_learner.analyze_style",
        AsyncMock(return_value=""),
    ) as analyze_mock:
        result = asyncio.run(_learn_and_persist(supervisor, mm))

    # No :intent keys → analyzer should NOT be called.
    assert not analyze_mock.called
    assert result is False


# ── BUG-001 tests ───────────────────────────────────────────────────────────


def test_store_turn_writes_intent_and_summary_separately():
    """_store_turn must write two distinct keys:
        turn:<n>:intent  → raw user intent
        turn:<n>:summary → assistant summary
    so the style analyzer never trains on prior GOAT summaries.
    """
    mm = _make_mm()

    asyncio.run(_store_turn(mm, 5, "ok", "Baaa Generale!"))

    assert mm.store.call_count == 2
    keys = [c.args[1] for c in mm.store.call_args_list]
    assert "turn:5:intent" in keys
    assert "turn:5:summary" in keys
    # Verify content split — no payload blending.
    by_key = {c.args[1]: c.args[2] for c in mm.store.call_args_list}
    assert by_key["turn:5:intent"] == "ok"
    assert by_key["turn:5:summary"] == "Baaa Generale!"


def test_learn_and_persist_reads_only_intent_keys():
    """When memory has both :intent and :summary entries, analyzer must
    receive ONLY the intent contents — never the summaries.
    """
    mm = _make_mm()
    mm.working.list = AsyncMock(return_value=[
        _make_entry("turn:5:intent", "ok"),
        _make_entry("turn:5:summary", "Baaa Generale!"),
        _make_entry("turn:4:intent", "hi there"),
        _make_entry("turn:4:summary", "Salut!"),
    ])
    supervisor = MagicMock()
    supervisor.memory_manager = mm

    with patch(
        "supervisor.behavior.store.load_style",
        AsyncMock(return_value=""),
    ), patch(
        "supervisor.behavior.style_learner.analyze_style",
        AsyncMock(return_value="length: terse"),
    ) as analyze_mock, patch(
        "supervisor.behavior.store.save_style",
        AsyncMock(return_value=True),
    ):
        asyncio.run(_learn_and_persist(supervisor, mm))

    # user_turns must contain only intent values, never summary values.
    user_turns = analyze_mock.call_args.args[0]
    assert user_turns == ["ok", "hi there"], (
        f"analyzer received summaries in user_turns: {user_turns}"
    )


def test_store_turn_role_is_session():
    """Sanity: store_turn uses SESSION_ROLE for the namespace."""
    mm = _make_mm()
    asyncio.run(_store_turn(mm, 1, "x", "y"))
    for call in mm.store.call_args_list:
        assert call.args[0] == SESSION_ROLE