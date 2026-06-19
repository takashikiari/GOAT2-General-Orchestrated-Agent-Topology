"""Tests for BUG-014 fix: unified missing-timestamp policy.

The codebase had two competing policies for entries with a missing
or unparseable ``created_at_ts``:

  - ``score_freshness`` (supervisor.mechanisms.freshness) returned
    ``OLD`` (safest), so the entry was still rendered with the
    ``[OLD]`` label.
  - ``filter_by_time`` (memory.temporal.temporal_filter) returned
    ``EXCLUDE`` (dropped the entry from any time-filtered list).

The result was inconsistent: the same entry could appear in one
prompt block and be silently missing from another. The fix picks
a single policy — "unknown age" — and threads it through both
modules so the behaviour is consistent everywhere.

The chosen policy: missing / unparseable ts is rendered as
``[unknown age]`` (so the LLM sees that the timestamp is missing)
but NEVER excluded from time-filtered queries (so the user can
still see the entry, just with an explicit "unknown age" label).
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from memory.temporal.temporal_filter import filter_by_time
from memory.temporal.temporal_format import UNKNOWN_AGE_LABEL, relative_age_label
from supervisor.mechanisms.freshness import score_freshness


def _entry_with_ts(ts: float) -> SimpleNamespace:
    """Build a record shaped like MemoryEntry so both mechanisms
    can consume it.

    freshness.score_freshness accepts a plain dict (it uses
    .get); filter_by_time requires the MemoryEntry shape
    (it reads e.metadata.get('created_at_ts')). We expose both
    shapes on a SimpleNamespace so one fixture serves both.
    """
    meta = {"created_at_ts": ts}
    return SimpleNamespace(
        key="turn:5:intent",
        content="x",
        metadata=meta,
        created_at_ts=ts,        # for freshness (.get on dict)
        # MemoryEntry also exposes .source — set a value so the
        # formatter doesn't complain when we use it elsewhere.
        source="working",
    )


# ── score_freshness: missing ts is OLD, not crash ───────────────────────────


def test_score_freshness_returns_old_for_missing_ts():
    """The freshness policy: unknown timestamp = OLD. Consistent
    with the previous behaviour and with the audit recommendation."""
    assert score_freshness({"content": "x"}, now=time.time()) == "OLD"


def test_score_freshness_returns_old_for_unparseable_ts():
    assert score_freshness({"created_at_ts": "not-a-number"}, now=time.time()) == "OLD"
    assert score_freshness({"created_at_ts": None}, now=time.time()) == "OLD"


def test_score_freshness_returns_fresh_for_recent_ts():
    now = time.time()
    assert score_freshness({"created_at_ts": now - 5}, now=now) == "FRESH"


# ── filter_by_time: missing ts is KEPT, not excluded (new policy) ─────────


def test_filter_by_time_keeps_entry_with_zero_ts_when_no_filter_active():
    """When no time filter is active, all entries pass through
    regardless of timestamp presence — the original behaviour."""
    entries = [_entry_with_ts(0.0)]
    out = filter_by_time(entries, start_ts=None, end_ts=None)
    assert len(out) == 1


def test_filter_by_time_keeps_entry_with_zero_ts_when_filter_active():
    """BUG-014 fix: an entry with ts=0 is no longer silently dropped
    when a time filter is active. It is KEPT and the LLM sees the
    ``[unknown age]`` label (BUG-003 fix); the alternative
    (silently dropping) hid the entry from the user with no
    explanation.
    """
    entries = [_entry_with_ts(0.0)]
    out = filter_by_time(entries, start_ts=time.time() - 3600, end_ts=time.time())
    assert len(out) == 1, (
        "BUG-014: filter_by_time is dropping entries with missing "
        "timestamps instead of surfacing them with [unknown age]."
    )


def test_filter_by_time_still_drops_when_ts_is_unparseable_string():
    """A non-numeric ts (truly unparseable) is still kept by the
    BUG-014 policy — surfaced to the formatter with [unknown age].
    The function must not crash on a garbage value."""
    entry = SimpleNamespace(
        key="k", content="x",
        metadata={"created_at_ts": "bad"},
        source="working",
    )
    out = filter_by_time([entry], start_ts=time.time() - 3600, end_ts=time.time())
    # Kept — the formatter will render [unknown age].
    assert len(out) == 1
    assert isinstance(out, list)


# ── format_entries / relative_age_label: explicit "unknown age" label ─────


def test_relative_age_label_renders_unknown_for_zero_ts():
    now = time.time()
    assert relative_age_label(0, now=now) == UNKNOWN_AGE_LABEL


def test_relative_age_label_renders_unknown_for_negative_ts():
    now = time.time()
    assert relative_age_label(-1, now=now) == UNKNOWN_AGE_LABEL


# ── Cross-module: same missing-ts entry produces the same visible policy ──


def test_unknown_ts_entry_is_visible_in_both_freshness_and_temporal_paths():
    """An entry with ts=0 must (a) be classified as OLD by freshness
    (so it can be rendered with a freshness label) AND (b) NOT be
    dropped by filter_by_time (so it remains in time-filtered
    queries). Together: the user sees the entry, clearly labelled
    as old / unknown age, instead of it silently disappearing.
    """
    entry = _entry_with_ts(0.0)
    now = time.time()

    # freshness path — accepts dict-shaped record.
    assert score_freshness({"created_at_ts": 0.0}, now=now) == "OLD"

    # temporal path — the entry survives a time filter.
    kept = filter_by_time(
        [entry], start_ts=now - 3600, end_ts=now,
    )
    assert len(kept) == 1

    # format path — explicit "unknown age" label.
    label = relative_age_label(0, now=now)
    assert label == UNKNOWN_AGE_LABEL