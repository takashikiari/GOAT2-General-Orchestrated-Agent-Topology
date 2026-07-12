"""tests.test_date_format — language-neutral timestamp/duration formatting.

Absolute timestamps render as ISO-8601 (zero natural-language content);
relative durations render as short English protocol phrases that the LLM
translates into the conversation's actual language — Python only owns the
arithmetic, never the target-language phrasing.
"""
from __future__ import annotations

from datetime import datetime

from memory.date_format import (
    format_duration,
    format_iso,
    format_relative,
    prefix_with_date,
)


def test_iso_format_is_timezone_aware_and_language_neutral():
    ts = 1_751_721_780.0
    iso = format_iso(ts)
    # Round-trips to the same instant and carries an explicit UTC offset
    # (not a bare local time) — both required so a downstream consumer can
    # disambiguate the moment without relying on locale-specific wording.
    assert datetime.fromisoformat(iso).timestamp() == ts
    assert iso[-6] in "+-" and iso[-3] == ":"


def test_prefix_with_date_uses_iso_not_locale_words():
    out = prefix_with_date("hello", 1_751_721_780.0)
    assert out.endswith("] hello")
    header = out[1:out.index("]")]
    assert datetime.fromisoformat(header).timestamp() == 1_751_721_780.0


def test_duration_under_a_minute():
    assert format_duration(30) == "under a minute"


def test_duration_minutes():
    assert format_duration(1200) == "20 min"


def test_duration_minutes_boundary_just_under_hour():
    assert format_duration(3599) == "59 min"


def test_duration_one_hour_singular():
    assert format_duration(3600) == "1 hour"


def test_duration_hours_plural():
    assert format_duration(7200) == "2 hours"


def test_duration_one_day_singular():
    assert format_duration(86400) == "1 day"


def test_duration_days_plural():
    assert format_duration(3 * 86400) == "3 days"


def test_duration_never_negative_on_clock_skew():
    assert format_duration(-5) == "under a minute"


def test_relative_just_now():
    now = 1_800_000_000.0
    assert format_relative(now - 10, now) == "just now"


def test_relative_minutes_ago():
    now = 1_800_000_000.0
    assert format_relative(now - 1200, now) == "20 min ago"


def test_relative_hours_ago():
    now = 1_800_000_000.0
    assert format_relative(now - 7200, now) == "2 hours ago"


def test_relative_beyond_horizon_falls_back_to_iso():
    now = 1_800_000_000.0
    ts = now - 40 * 86400
    assert format_relative(ts, now) == format_iso(ts)


def test_relative_never_negative_on_clock_skew():
    now = 1_800_000_000.0
    assert format_relative(now + 5, now) == "just now"
