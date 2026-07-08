"""tests.test_temporal_route — parse_interval correctness (dateparser-based).

Replaces the old hand-rolled token-walk parser test suite. The old parser
required an explicit day+month pair and HH:MM-formatted time, and only ever
saw GLiNER-extracted DATE/TIME entity text — never the raw query. Confirmed
on real data (2026-07-08): GLiNER detects zero entities for relative Romanian
expressions like "ieri" or "acum 2 ore", so the old pipeline silently never
routed those queries temporally at all. parse_interval now takes the raw
query text directly and uses dateparser.search.search_dates, which handles
relative expressions natively.
"""
from __future__ import annotations

from datetime import datetime

from memory.temporal_route import parse_interval

_NOW = datetime(2026, 7, 7, 10, 0, 0)  # stable reference: Tuesday 2026-07-07 10:00


class TestAbsoluteDates:
    def test_full_romanian_month(self):
        result = parse_interval("Ce mi-ai spus pe 4 iulie?", now=_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 4, 12, 0, 0).timestamp()
        assert abs(after - (center - 12 * 3600)) < 1
        assert abs(before - (center + 12 * 3600)) < 1

    def test_date_with_explicit_year(self):
        result = parse_interval("Ce mi-ai spus pe 4 iulie 2026?", now=_NOW)
        assert result is not None

    def test_date_and_time_narrows_window(self):
        result = parse_interval("Ce am discutat pe 4 iulie 07:00?", now=_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 4, 7, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1

    def test_future_date_resolves_to_past_year(self):
        """31 decembrie relative to 2026-07-07 must resolve to 2025-12-31 (past),
        not 2026-12-31 (future) — PREFER_DATES_FROM='past' handles this natively,
        replacing the old parser's manual year-1 retry."""
        result = parse_interval("Ce mi-ai spus pe 31 decembrie?", now=_NOW)
        assert result is not None
        after, _ = result
        rolled_center = datetime(2025, 12, 31, 12, 0, 0).timestamp()
        assert abs(after - (rolled_center - 12 * 3600)) < 1


class TestRelativeExpressions:
    """The entire point of this rewrite: these never matched anything before."""

    def test_yesterday(self):
        result = parse_interval("Ce am discutat ieri despre asta?", now=_NOW)
        assert result is not None
        after, before = result
        yesterday_noon = datetime(2026, 7, 6, 12, 0, 0).timestamp()
        assert abs(after - (yesterday_noon - 12 * 3600)) < 1

    def test_hours_ago(self):
        result = parse_interval("Ce am vorbit acum 2 ore?", now=_NOW)
        assert result is not None
        after, before = result
        two_hours_ago = datetime(2026, 7, 7, 8, 0, 0).timestamp()
        # Narrow (time-specific) window since a concrete hour is implied.
        assert after <= two_hours_ago <= before


class TestNoMatch:
    def test_no_temporal_expression_returns_none(self):
        result = parse_interval("Care a fost cauza confuziei cu logurile între mine și tine?", now=_NOW)
        assert result is None

    def test_empty_query_returns_none(self):
        assert parse_interval("", now=_NOW) is None
