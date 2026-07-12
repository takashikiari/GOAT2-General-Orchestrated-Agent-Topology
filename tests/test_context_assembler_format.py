"""tests.test_context_assembler_format — format_messages relative-time prefixes + session gaps."""
from __future__ import annotations

from memory.context_assembler import format_messages

_NOW = 1_800_000_000.0


def _msg(role: str, content: str, ts: float) -> dict:
    return {"role": role, "content": content, "timestamp": ts}


def test_prefixes_each_message_with_relative_time():
    msgs = [_msg("user", "salut", _NOW - 1200)]
    assert format_messages(msgs, now=_NOW) == "[20 min ago] user: salut"


def test_no_prefix_when_timestamp_missing():
    msgs = [{"role": "user", "content": "salut"}]
    assert format_messages(msgs, now=_NOW) == "user: salut"


def test_no_prefix_when_timestamp_falsy_zero():
    """timestamp: 0.0 (the _msg() helper default used elsewhere in the suite) must render
    exactly like 'missing' — this is what keeps tests/test_context_budget.py passing unchanged."""
    msgs = [_msg("user", "salut", 0.0)]
    assert format_messages(msgs, now=_NOW) == "user: salut"


def test_no_gap_marker_within_session():
    msgs = [_msg("user", "a", _NOW - 120), _msg("assistant", "b", _NOW - 60)]
    out = format_messages(msgs, now=_NOW)
    assert "gap:" not in out


def test_gap_marker_inserted_across_session_boundary():
    """SESSION_GAP_SECONDS default is 1800s; a ~2h gap must surface a marker between b and c."""
    msgs = [
        _msg("user", "a", _NOW - 7260),
        _msg("assistant", "b", _NOW - 7200),
        _msg("user", "c", _NOW - 60),
    ]
    lines = format_messages(msgs, now=_NOW).split("\n")
    assert lines[0].endswith("user: a")
    assert lines[1].endswith("assistant: b")
    assert lines[2].startswith("--- gap:")
    assert lines[3].endswith("user: c")


def test_default_now_uses_wall_clock_when_omitted():
    import time
    msgs = [_msg("user", "salut", time.time())]
    assert format_messages(msgs) == "[just now] user: salut"
