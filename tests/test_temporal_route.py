"""tests.test_temporal_route — parse_interval correctness."""
from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import patch

import pytest

from memory.temporal_route import parse_interval


def _fixed_now() -> float:
    """Stable reference: 2026-07-07 10:00:00 UTC."""
    return datetime(2026, 7, 7, 10, 0, 0).timestamp()


def _now_year() -> int:
    return 2026


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    """Freeze datetime.now() and time.time() to a stable reference point."""
    fixed = _fixed_now()
    monkeypatch.setattr("memory.temporal_route.time", type("t", (), {"time": staticmethod(lambda: fixed)})())
    real_datetime = datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 7, 10, 0, 0)

    monkeypatch.setattr("memory.temporal_route.datetime", _FakeDatetime)


def _interval(entities, types=None):
    if types is None:
        types = ["date"] * len(entities)
    return parse_interval(entities, types)


class TestDateOnly:
    def test_full_romanian_month(self):
        result = _interval(["4 iulie"])
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 4, 12, 0, 0).timestamp()
        assert abs(after - (center - 12 * 3600)) < 1
        assert abs(before - (center + 12 * 3600)) < 1

    def test_abbreviated_month(self):
        result = _interval(["4 iul"])
        assert result is not None

    def test_all_romanian_months_parse(self):
        months = [
            ("ian", 1), ("feb", 2), ("mar", 3), ("apr", 4), ("mai", 5),
            ("iun", 6), ("iul", 7), ("aug", 8), ("sep", 9), ("oct", 10),
            ("nov", 11), ("dec", 12),
        ]
        for abbr, expected_month in months:
            r = _interval([f"15 {abbr}"])
            assert r is not None, f"failed for {abbr}"

    def test_date_with_explicit_year(self):
        result = _interval(["4 iulie 2026"])
        assert result is not None

    def test_invalid_day_returns_none(self):
        result = _interval(["32 iulie"])
        assert result is None  # datetime(2026,7,32) raises ValueError

    def test_no_date_returns_none(self):
        result = _interval(["Letta", "API"], ["organization", "technology"])
        assert result is None

    def test_empty_entities_returns_none(self):
        assert parse_interval([], []) is None


class TestDateWithTime:
    def test_date_and_time_narrows_window(self):
        result = _interval(["4 iulie 07:00"])
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 4, 7, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1

    def test_time_in_separate_entity(self):
        result = parse_interval(["4 iulie", "07:00"], ["date", "time"])
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 4, 7, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1

    def test_midnight_time(self):
        result = _interval(["4 iulie 00:00"])
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 4, 0, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1


class TestFallbackToAllEntities:
    def test_event_label_still_parses_via_fallback(self):
        """If GLiNER labels '4 iulie' as 'event', fallback scans all texts."""
        result = parse_interval(["4 iulie", "Letta"], ["event", "organization"])
        assert result is not None

    def test_no_date_text_in_non_date_entities_returns_none(self):
        result = parse_interval(["Letta", "GOAT"], ["organization", "project"])
        assert result is None


class TestFutureYearRollback:
    def test_future_date_rolls_to_previous_year(self):
        # 4 august is in the future relative to 2026-07-07; BUT it's only ~28 days away,
        # not > 1 day in the future... let's test with a clearly past-year scenario.
        # Dec 31 relative to July 7: 31 dec 2026 is > today, stays 2026 (within same year).
        # A month like "4 August" is in 2026 and ~28 days in future — not rolled back.
        # Test the rollback specifically: parse a date > 1 day future.
        result = _interval(["31 decembrie"])
        assert result is not None
        after, _ = result
        # 31 dec 2026 is in the future but < 1 day from now? No, ~177 days ahead.
        # The rollback fires when after_ts > now + 86400 (tomorrow).
        # 31 dec 2026 ~ 177 days ahead → should roll back to 31 dec 2025.
        rolled_center = datetime(2025, 12, 31, 12, 0, 0).timestamp()
        assert abs(after - (rolled_center - 12 * 3600)) < 1
