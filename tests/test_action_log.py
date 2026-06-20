"""Tests for the structured action log feature.

When asked "what did you do?", GOAT used to confabulate from
its own previous text — the visible response is a fallback
("[Reached the 6-tool per-turn limit...]") that doesn't tell
the model which tools succeeded or failed.

The fix: every turn persists a structured JSON record of
called tools + their outcomes. On the NEXT turn, the [Present]
layer renders this record as a concrete "Last turn actions: …"
list — names of tools, args, success/failure — so the model
can report from data, not from its own previous text.

The persistence key is ``turn:<N>:actions`` (sibling of
``turn:<N>:intent`` and ``turn:<N>:summary``). The format is
a small JSON list of dicts, e.g.::

    [{"tool": "memory_delete",
      "args": {"key": "turn_1781800064_18"},
      "ok": false,
      "summary": "Key not found"},
     {"tool": "memory_recent",
      "args": {"tier": "any", "limit": 30},
      "ok": true,
      "summary": "4 entries"}]
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from supervisor.session import turn_persistence
from supervisor.session.turn_persistence import (
    _ACTION_LOG_KEY,
    _action_log_from_turn,
    format_action_log,
    store_action_log,
)


# ── format_action_log ──────────────────────────────────────────────────────


def test_format_action_log_renders_ok_and_fail_differently():
    """A successful tool call renders as ``tool(args) → ok:summary``;
    a failed one renders as ``tool(args) → FAIL:summary``. The visual
    distinction lets the model report successes and failures
    correctly without parsing."""
    entries = [
        {"tool": "memory_delete", "args": {"key": "x"}, "ok": True,  "summary": "deleted"},
        {"tool": "memory_get",    "args": {"key": "y"}, "ok": False, "summary": "Key not found"},
    ]
    out = format_action_log(entries)
    assert "memory_delete" in out
    assert "ok: deleted" in out
    assert "memory_get" in out
    assert "FAIL: Key not found" in out


def test_format_action_log_includes_args():
    """Args are included so the model can match tool calls to
    user intent (e.g. 'which key did you delete?')."""
    entries = [
        {"tool": "memory_delete", "args": {"key": "turn_X"}, "ok": False, "summary": "Key not found"},
    ]
    out = format_action_log(entries)
    assert "turn_X" in out
    assert "key" in out or "key=" in out  # either 'key=turn_X' or 'key: turn_X' format


def test_format_action_log_handles_empty_list():
    """No actions (model answered directly) → empty string.
    Caller decides whether to render the section header."""
    assert format_action_log([]) == ""


def test_format_action_log_truncates_long_summaries():
    """Tool outputs can be hundreds of characters. The log
    must truncate so it fits in the prompt budget."""
    long = "x" * 5000
    entries = [
        {"tool": "shell_run", "args": {"command": "ls"}, "ok": True, "summary": long},
    ]
    out = format_action_log(entries)
    # Per-entry summary cap is _ACTION_SUMMARY_CAP (200 chars).
    # The full line is roughly: "- shell_run → ok: " (16) + 200 summary
    # = ~216 chars. Verify the summary content (the repeated 'x')
    # is truncated, but the rest of the line structure is preserved.
    summary_chars_in_line = out.count("x")
    assert summary_chars_in_line <= 200, (
        f"summary not truncated — found {summary_chars_in_line} chars, "
        f"max 200"
    )


# ── _action_log_from_turn ──────────────────────────────────────────────────


def test_action_log_from_turn_builds_structured_entries():
    """A turn result with called_tools and tool_results produces
    one entry per tool call, with args and a short summary."""
    turn = SimpleNamespace(
        called_tools=("memory_recent", "memory_delete", "memory_get"),
        tool_results=(
            "4 entries",
            "Key not found: 'turn_X'",
            "Content: hello world",
        ),
    )
    log = _action_log_from_turn(turn)
    assert len(log) == 3
    assert log[0]["tool"] == "memory_recent"
    assert log[0]["args"] == {}
    assert log[0]["ok"] is True
    assert log[0]["summary"] == "4 entries"
    assert log[1]["tool"] == "memory_delete"
    assert log[1]["ok"] is False
    assert "Key not found" in log[1]["summary"]
    assert log[2]["tool"] == "memory_get"
    assert log[2]["ok"] is True
    assert "hello world" in log[2]["summary"]


def test_action_log_marks_failure_on_error_keyword():
    """A tool result starting with 'ERROR' is flagged ok=False
    even if called_tools records a success — the actual outcome
    is in the result, not the call dispatch."""
    turn = SimpleNamespace(
        called_tools=("memory_get",),
        tool_results=("ERROR: Key not found: 'foo'",),
    )
    log = _action_log_from_turn(turn)
    assert log[0]["ok"] is False
    assert "Key not found" in log[0]["summary"]


def test_action_log_handles_mismatched_lengths():
    """Defensive: if called_tools and tool_results have different
    lengths (shouldn't happen but might during partial failures),
    zip truncates to the shorter."""
    turn = SimpleNamespace(
        called_tools=("a", "b", "c"),
        tool_results=("r1",),  # only 1 result
    )
    log = _action_log_from_turn(turn)
    # Only 1 entry, not 3.
    assert len(log) == 1
    assert log[0]["tool"] == "a"


# ── store_action_log ──────────────────────────────────────────────────────


def test_store_action_log_writes_to_working_memory():
    """The log is persisted to working memory under a stable
    key so the next turn can read it."""
    async def _run():
        mm = MagicMock()
        mm.store = AsyncMock()
        turn = SimpleNamespace(
            called_tools=("memory_delete",),
            tool_results=("Key not found: 'foo'",),
        )
        await store_action_log(mm, turn_count=5, turn=turn)
        # Must have called mm.store with the actions key.
        assert mm.store.called
        args, key, payload = mm.store.call_args.args
        assert key == _ACTION_LOG_KEY.format(n=5)
        parsed = json.loads(payload)
        assert len(parsed) == 1
        assert parsed[0]["tool"] == "memory_delete"
        assert parsed[0]["ok"] is False
    asyncio.run(_run())


def test_store_action_log_swallows_mm_failure():
    """A persistence failure must NOT crash the turn — best-effort."""
    async def _run():
        mm = MagicMock()
        mm.store = AsyncMock(side_effect=RuntimeError("redis down"))
        turn = SimpleNamespace(called_tools=("x",), tool_results=("y",))
        # Must not raise.
        await store_action_log(mm, turn_count=1, turn=turn)
    asyncio.run(_run())


def test_store_action_log_skips_empty_turn():
    """A turn with no tool calls (direct reply) doesn't waste
    a working-memory entry on an empty log."""
    async def _run():
        mm = MagicMock()
        mm.store = AsyncMock()
        turn = SimpleNamespace(called_tools=(), tool_results=())
        await store_action_log(mm, turn_count=1, turn=turn)
        assert not mm.store.called
    asyncio.run(_run())


# ── Integration with the [Present] layer ─────────────────────────────────


def test_present_layer_renders_last_turn_actions():
    """When action logs exist in working memory, the [Present]
    layer renders them as a 'Last turn actions:' block BEFORE
    the working-memory entries — so GOAT sees the structured
    record first when asked 'what did you do?'."""
    from supervisor.session.layer_renderer import render_present_layer
    from supervisor.session import layer_renderer

    last_actions = [
        {"tool": "memory_delete", "args": {"key": "X"}, "ok": False, "summary": "Key not found"},
    ]
    # Stub the actions fetch.
    layer_renderer._load_last_turn_actions = lambda records, mm: (
        last_actions if any(r.get("key", "").endswith(":actions") for r in records) else []
    )
    # Build a working-memory list with one summary entry and
    # one actions entry. The [Present] renderer must show BOTH,
    # with actions labelled.
    records = [
        SimpleNamespace(
            key="turn:7:actions", content=json.dumps(last_actions),
            metadata={"created_at_ts": time.time() - 5}, source="working",
        ),
        SimpleNamespace(
            key="turn:7:summary", content="User asked me to delete X",
            metadata={"created_at_ts": time.time() - 5}, source="working",
        ),
    ]
    out = render_present_layer(records, now=time.time(), max_entries=50)
    # Action log must appear in the rendered block.
    assert "Last turn actions" in out, (
        f"action log must be rendered in [Present]; got:\n{out}"
    )
    assert "memory_delete" in out
    # The structured failure is visible.
    assert "FAIL" in out or "Key not found" in out