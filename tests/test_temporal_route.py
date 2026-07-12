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


# Reference point matching the real conversation where this bug was caught
# live: "9 iulie" (July 9) is in the PAST relative to this "now", so
# PREFER_DATES_FROM="past" should resolve it to the current year, 2026 — not
# roll it back further. The bug (dateparser misreading a colon-less spoken
# hour, e.g. "ora 19", as the 2-digit YEAR 2019) is orthogonal to that
# year-rollback behavior, so it must reproduce here too.
_BUG_NOW = datetime(2026, 7, 12, 10, 0, 0)


class TestSpokenHourWithoutColon:
    """Regression coverage for the live-confirmed bug: 'la ora N' / 'ora N'
    without a colon was misread by dateparser as a 2-digit YEAR, not an hour.
    """

    def test_la_ora_19_resolves_to_2026_not_2019(self):
        result = parse_interval("9 iulie la ora 19", now=_BUG_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 9, 19, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1

    def test_la_ora_20_resolves_to_2026_not_2020(self):
        result = parse_interval("9 iulie la ora 20", now=_BUG_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 9, 20, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1

    def test_la_ora_single_digit_now_resolves(self):
        """Before the fix this combination returned None outright (dateparser
        found no match at all, rather than merely a wrong year) — confirm the
        colon-normalization fix also resolves this case, not just the
        wrong-year one."""
        result = parse_interval("9 iulie la ora 9", now=_BUG_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 9, 9, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1

    def test_bare_ora_single_digit(self):
        """'ora 9' with no date at all anchors to today (RELATIVE_BASE)."""
        result = parse_interval("ora 9", now=_BUG_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 12, 9, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1

    def test_already_colon_form_still_works(self):
        """Regression guard: the already-correct 'ora 19:00' form (which
        never had this bug) must not be touched/broken by the colon-less
        normalization regex."""
        result = parse_interval("9 iulie ora 19:00", now=_BUG_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 9, 19, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1


class TestImplausibleYearGuard:
    """Defense-in-depth: even if a future phrasing this preprocessing step
    doesn't cover triggers the same class of dateparser misparse (a bare
    number read as a 2-digit year), parse_interval must not silently return
    a wrong-decade window. Simulated via monkeypatch since no known live
    phrasing besides the one already fixed above reproduces it.
    """

    def test_implausible_year_without_retry_option_returns_none(self, monkeypatch):
        import memory.temporal_route as mod

        def fake_search_dates(text, languages=None, settings=None):
            return [("15 mai", datetime(2003, 5, 15, 0, 0))]

        monkeypatch.setattr(mod, "search_dates", fake_search_dates)
        result = mod.parse_interval("15 mai", now=_BUG_NOW)
        assert result is None

    def test_implausible_year_retries_without_hour_phrase(self, monkeypatch):
        import memory.temporal_route as mod

        calls = []

        def fake_search_dates(text, languages=None, settings=None):
            calls.append(text)
            if "ora" in text:
                # Simulate the misparse: hour phrase drags in a bogus year.
                return [(text, datetime(2003, 5, 15, 0, 0))]
            # Retry (hour phrase stripped) lands on a plausible year.
            return [(text, datetime(2026, 5, 15, 0, 0))]

        monkeypatch.setattr(mod, "search_dates", fake_search_dates)
        result = mod.parse_interval("15 mai la ora 8", now=_BUG_NOW)
        assert result is not None
        assert len(calls) == 2  # primary call + implausible-year retry
        after, before = result
        # Retry match has no hour marker in its matched_text -> day-level window.
        center = datetime(2026, 5, 15, 12, 0, 0).timestamp()
        assert abs(after - (center - 12 * 3600)) < 1
        assert abs(before - (center + 12 * 3600)) < 1


class TestMultipleMatches:
    """When search_dates returns multiple date/time fragments for the same
    query (e.g. a vague day anchor plus a precise time), prefer the match
    that carries time-of-day precision over an earlier, vaguer one — a bare
    day reference must not shadow a more specific time in the same query.
    """

    def test_vague_day_plus_precise_time_prefers_the_time_match(self):
        """Confirmed live 2026-07-12, real incident time (17:22, after noon —
        so 'la 12:00' unambiguously means earlier today, not a future time
        rolled back a day by PREFER_DATES_FROM='past'): 'Dar azi pe la 12:00
        ce am discutat?' made dateparser return two matches — ('azi', now)
        and ('la 12:00', today at noon) — and parse_interval used matches[0]
        ('azi', with no time marker), producing a full-day window instead of
        the intended ±1h-around-noon one, which let same-day unrelated
        content crowd out the actually-requested moment downstream."""
        incident_now = datetime(2026, 7, 12, 17, 22, 0)
        result = parse_interval("Dar azi pe la 12:00 ce am discutat?", now=incident_now)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 12, 12, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1

    def test_single_match_unaffected(self):
        """Regression guard: queries with exactly one match (the common case,
        every other test in this file) are untouched by the preference loop."""
        result = parse_interval("Ce mi-ai spus pe 4 iulie 07:00?", now=_NOW)
        assert result is not None
        after, before = result
        center = datetime(2026, 7, 4, 7, 0, 0).timestamp()
        assert abs(after - (center - 3600)) < 1
        assert abs(before - (center + 3600)) < 1


class TestKnownUnparseableGaps:
    """Documented gaps, not fixed here (see temporal_route.py module
    docstring): both require a hand-rolled relative-expression lexicon,
    disproportionate effort for this bugfix's scope."""

    def test_luni_monday_unparseable(self):
        """'luni' ("Monday") collides with dateparser's month-token matching
        in this language configuration and returns no match."""
        assert parse_interval("luni", now=_NOW) is None

    def test_alaltaieri_day_before_yesterday_unparseable(self):
        """'alaltaieri'/'alaltăieri' ("day before yesterday") is not in
        dateparser's RO relative-date lexicon (checked with diacritics
        restored too) and returns no match."""
        assert parse_interval("alaltaieri", now=_NOW) is None
        assert parse_interval("alaltăieri", now=_NOW) is None
