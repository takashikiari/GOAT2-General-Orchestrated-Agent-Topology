"""Tests for temporal_format — relative-age labels in format_entries.

Verifies BUG-003: memory entries presented to the LLM must carry a
relative-age prefix so the model can distinguish "this was a fact
five minutes ago" from "this was a fact five days ago" — and from
entries with no recorded timestamp at all.
"""
from __future__ import annotations

from types import SimpleNamespace

from memory.temporal.temporal_format import (
    DEFAULT_DAY_THRESHOLD_S,
    DEFAULT_FRESH_THRESHOLD_S,
    DEFAULT_RECENT_THRESHOLD_S,
    UNKNOWN_AGE_LABEL,
    format_entries_with_age,
    load_temporal_config,
    relative_age_label,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _entry(key: str, content: str, created_at_ts: float, source: str = "working") -> SimpleNamespace:
    return SimpleNamespace(
        source=source,
        key=key,
        content=content,
        metadata={"created_at_ts": created_at_ts},
    )


_CFG = load_temporal_config()


# ── relative_age_label ──────────────────────────────────────────────────────


def test_relative_age_seconds_when_very_recent():
    now = 1_700_000_000.0
    assert relative_age_label(now - 5, now=now, cfg=_CFG) == "[5s ago]"


def test_relative_age_minutes_when_within_hour():
    now = 1_700_000_000.0
    # 120 seconds = 2 minutes
    assert relative_age_label(now - 120, now=now, cfg=_CFG) == "[2m ago]"


def test_relative_age_hours_when_within_day():
    now = 1_700_000_000.0
    # 3 hours
    assert relative_age_label(now - 3 * 3600, now=now, cfg=_CFG) == "[3h ago]"


def test_relative_age_days_when_older_than_day():
    now = 1_700_000_000.0
    # 5 days
    assert relative_age_label(now - 5 * 86400, now=now, cfg=_CFG) == "[5d ago]"


def test_relative_age_unknown_when_zero():
    now = 1_700_000_000.0
    assert relative_age_label(0, now=now, cfg=_CFG) == UNKNOWN_AGE_LABEL


def test_relative_age_unknown_when_missing():
    now = 1_700_000_000.0
    assert relative_age_label(None, now=now, cfg=_CFG) == UNKNOWN_AGE_LABEL


def test_relative_age_unknown_when_unparseable():
    now = 1_700_000_000.0
    assert relative_age_label("not-a-number", now=now, cfg=_CFG) == UNKNOWN_AGE_LABEL


def test_relative_age_clamps_negative_age_to_zero():
    """Future timestamp (clock skew) must not produce a negative age label."""
    now = 1_700_000_000.0
    # ts is 100s in the future; age clamps to 0
    assert relative_age_label(now + 100, now=now, cfg=_CFG) == "[0s ago]"


# ── format_entries_with_age ─────────────────────────────────────────────────


def test_format_entries_empty_returns_empty_string():
    assert format_entries_with_age([], now=0.0, cfg=_CFG) == ""


def test_format_entries_includes_age_source_key_content():
    now = 1_700_000_000.0
    e = _entry("turn:5:intent", "ok", now - 60, source="working")
    out = format_entries_with_age([e], now=now, cfg=_CFG)
    assert out == "[1m ago] [working] turn:5:intent: ok"


def test_format_entries_truncates_long_content():
    now = 1_700_000_000.0
    long = "x" * 500
    e = _entry("k", long, now - 5, source="working")
    out = format_entries_with_age([e], max_content_len=200, now=now, cfg=_CFG)
    # Content section is exactly 200 chars
    assert out.endswith(": " + "x" * 200)


def test_format_entries_renders_unknown_age_when_no_metadata():
    """Entry without metadata dict must not crash; must label [unknown age]."""
    now = 1_700_000_000.0
    e = SimpleNamespace(source="working", key="k", content="x")  # no .metadata
    out = format_entries_with_age([e], now=now, cfg=_CFG)
    assert out.startswith(UNKNOWN_AGE_LABEL)


def test_format_entries_renders_unknown_age_when_ts_zero():
    now = 1_700_000_000.0
    e = _entry("k", "x", 0.0)
    out = format_entries_with_age([e], now=now, cfg=_CFG)
    assert out.startswith(UNKNOWN_AGE_LABEL)


def test_format_entries_multiple_lines():
    now = 1_700_000_000.0
    es = [
        _entry("a", "alpha", now - 5),
        _entry("b", "beta",  now - 3 * 3600),
        _entry("c", "gamma", 0.0),
    ]
    out = format_entries_with_age(es, now=now, cfg=_CFG)
    lines = out.split("\n")
    assert len(lines) == 3
    assert "[5s ago]" in lines[0]
    assert "[3h ago]" in lines[1]
    assert UNKNOWN_AGE_LABEL in lines[2]


# ── load_temporal_config ────────────────────────────────────────────────────


def test_load_temporal_config_returns_defaults_when_no_section():
    cfg = load_temporal_config()
    assert cfg["show_relative_age"] is True
    assert cfg["fresh_threshold_s"]  == DEFAULT_FRESH_THRESHOLD_S
    assert cfg["recent_threshold_s"] == DEFAULT_RECENT_THRESHOLD_S
    assert cfg["day_threshold_s"]    == DEFAULT_DAY_THRESHOLD_S


def test_load_temporal_config_uses_module_cached_cfg():
    """The module-level _CFG must be populated by the same call."""
    from memory.temporal import temporal_format as tf
    assert tf._CFG == _CFG  # same dict object — proves the cache is hit