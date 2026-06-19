"""Tests for build_context staleness wiring (BUG-004).

Verifies that ``_format_line`` prepends ``[STALE]`` to working-memory
lines whose entry is a DAG result older than the configured
``dag_max_age_seconds`` AND whose intent does not mention DAG-related
keywords.
"""
from __future__ import annotations

import time

from supervisor.mechanisms.context_builder import _format_line
from supervisor.mechanisms.staleness import STALE_PREFIX


def _record(key: str, content: str, ts: float) -> dict:
    return {"key": key, "content": content, "created_at_ts": ts}


def test_format_line_prepends_stale_for_old_dag_non_dag_intent():
    """Old DAG entry + non-DAG intent → must be marked [STALE]."""
    now = time.time()
    rec = _record("dag:result:abc", "old result", ts=now - 1000)
    line = _format_line(rec, now=now, intent="hello how are you")
    assert line.startswith(f"{STALE_PREFIX} "), (
        f"expected STALE_PREFIX, got: {line!r}"
    )


def test_format_line_keeps_fresh_dag_unflagged():
    """Recent DAG entry (< dag_max_age_seconds) → must NOT be marked stale."""
    now = time.time()
    rec = _record("dag:result:abc", "fresh result", ts=now - 1)
    line = _format_line(rec, now=now, intent="hello")
    assert not line.startswith(f"{STALE_PREFIX} ")


def test_format_line_keeps_old_dag_when_intent_asks_for_dag():
    """Old DAG entry + intent mentions 'dag' → must NOT be stale (user wants it)."""
    now = time.time()
    rec = _record("dag:result:abc", "old but asked for", ts=now - 1000)
    line = _format_line(rec, now=now, intent="show me the dag result")
    assert not line.startswith(f"{STALE_PREFIX} ")


def test_format_line_never_marks_non_dag_entries_stale():
    """A turn: entry is CONV namespace — staleness only applies to DAG."""
    now = time.time()
    rec = _record("turn:5:intent", "hi", ts=now - 100000)
    line = _format_line(rec, now=now, intent="hello")
    assert not line.startswith(f"{STALE_PREFIX} ")


def test_format_line_handles_unparseable_timestamp_safely():
    """Missing/garbage created_at_ts must not crash."""
    now = time.time()
    rec = {"key": "dag:result:x", "content": "x"}  # no created_at_ts
    line = _format_line(rec, now=now, intent="hi")
    # Unparseable ts on a DAG entry IS considered stale by is_stale().
    assert line.startswith(f"{STALE_PREFIX} ") or "[OLD]" in line


def test_format_line_handles_non_dict_input():
    """Defensive: non-dict inputs return empty string, not crash."""
    now = time.time()
    assert _format_line(None, now=now, intent="hi") == ""
    assert _format_line("not a dict", now=now, intent="hi") == ""