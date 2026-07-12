"""tests.test_date_format — Romanian relative-time and duration formatting."""
from __future__ import annotations

from memory.date_format import format_duration_ro, format_relative_ro, format_ro_date


def test_duration_under_a_minute():
    assert format_duration_ro(30) == "sub un minut"


def test_duration_minutes():
    assert format_duration_ro(1200) == "20 min"


def test_duration_minutes_boundary_just_under_hour():
    assert format_duration_ro(3599) == "59 min"


def test_duration_one_hour_singular():
    assert format_duration_ro(3600) == "1 oră"


def test_duration_hours_plural():
    assert format_duration_ro(7200) == "2 ore"


def test_duration_one_day_singular():
    assert format_duration_ro(86400) == "1 zi"


def test_duration_days_plural():
    assert format_duration_ro(3 * 86400) == "3 zile"


def test_duration_never_negative_on_clock_skew():
    assert format_duration_ro(-5) == "sub un minut"


def test_relative_just_now():
    now = 1_800_000_000.0
    assert format_relative_ro(now - 10, now) == "chiar acum"


def test_relative_minutes_ago():
    now = 1_800_000_000.0
    assert format_relative_ro(now - 1200, now) == "acum 20 min"


def test_relative_hours_ago():
    now = 1_800_000_000.0
    assert format_relative_ro(now - 7200, now) == "acum 2 ore"


def test_relative_beyond_horizon_falls_back_to_absolute_date():
    now = 1_800_000_000.0
    ts = now - 40 * 86400
    assert format_relative_ro(ts, now) == format_ro_date(ts)


def test_relative_never_negative_on_clock_skew():
    now = 1_800_000_000.0
    assert format_relative_ro(now + 5, now) == "chiar acum"
