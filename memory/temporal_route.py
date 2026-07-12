"""memory.temporal_route — parse a date/time expression embedded in a query.

Uses dateparser.search.search_dates to find and parse date/time expressions
directly from the raw query text — absolute ("4 iulie") and relative ("ieri",
"acum 2 ore") alike, natively in Romanian. Replaces a hand-rolled token-walk
parser that required an explicit day+month token pair and only ever saw
GLiNER-extracted DATE/TIME entity text. Confirmed on real data (2026-07-08):
GLiNER detects zero entities for relative Romanian expressions like "ieri" or
"acum 2 ore", so the old GLiNER-gated pipeline never routed those queries
temporally at all, regardless of parser quality.

Known gaps (checked live, not fixed here — see task audit 2026-07-12):
"luni" ("Monday") is genuinely ambiguous for dateparser (collides with month
tokens) and "alaltaieri"/"alaltăieri" ("day before yesterday") isn't in
dateparser's RO relative-date lexicon even with diacritics restored — both
return no match with languages=["ro", "en"]. Fixing either would mean adding
a hand-rolled relative-expression lexicon, which is disproportionate to a
single-word gap; documented here instead of scope-creeping into this bugfix.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from dateparser.search import search_dates

from memory.config_extra import TEMPORAL_MAX_YEARS_IN_PAST

__all__ = ["parse_interval"]

# Words/symbols in the matched span implying a specific hour was given ->
# narrow ±1h window. Their absence means a day-level match -> ±12h window
# centered on noon (mirrors the old parser's date-only behavior).
_TIME_MARKERS = (":", "oră", "ora", "orei", "ore", "minut")

# Spoken-hour phrasing without a colon ("la ora 19", "ora 9") — matches
# "ora" (optionally preceded by "la") followed by a 1-2 digit hour that is
# NOT already followed by ":MM". The \b after the digits (rather than a
# lookahead alone) blocks regex backtracking from re-matching a partial
# 1-digit prefix of an already-colon'd 2-digit hour (e.g. "ora 19:00" must
# NOT be rewritten as "ora 1:009:00" — verified live before landing this).
_SPOKEN_HOUR_RE = re.compile(r"\b((?:la\s+)?ora)\s+(\d{1,2})\b(?!\s*:)", re.IGNORECASE)

# Trailing "(la) ora N[:MM]" phrase, stripped during the implausible-year
# retry in parse_interval (see _IMPLAUSIBLE_YEAR guard below).
_TRAILING_HOUR_PHRASE_RE = re.compile(r"\s*\b(?:la\s+)?ora\s+\d{1,2}(?::\d{2})?\.?\s*$", re.IGNORECASE)


def _normalize_spoken_hours(query: str) -> str:
    """Rewrite "la ora N" / "ora N" (no colon) into explicit "N:00" form.

    Bug confirmed live 2026-07-12: dateparser.search.search_dates reads a
    colon-less spoken hour as a bare number and grafts it onto the matched
    date as a 2-digit YEAR instead of a time-of-day — "9 iulie la ora 19"
    parsed as datetime(2019, 7, 9), "...ora 20" as datetime(2020, 7, 9).
    Giving dateparser an explicit "19:00" makes it read the number as a time,
    matching how "9 iulie ora 19:00" already parsed correctly.
    """

    def repl(m: re.Match) -> str:
        hour = int(m.group(2))
        if hour > 23:  # not a plausible hour — leave whatever this actually is alone
            return m.group(0)
        return f"{m.group(1)} {hour}:00"

    return _SPOKEN_HOUR_RE.sub(repl, query)


def _is_plausible_year(year: int, now: datetime) -> bool:
    return now.year - TEMPORAL_MAX_YEARS_IN_PAST <= year <= now.year


def parse_interval(query: str, now: datetime | None = None) -> tuple[float, float] | None:
    """Find a date/time expression in ``query``; return an (after_ts, before_ts) window.

    Returns ``None`` when no date/time expression is found. ``now`` anchors
    relative expressions ("ieri", "acum 2 ore") and biases ambiguous dates
    toward the past (recall queries are about past events) — defaults to the
    real current time; tests pass a fixed value.
    """
    now = now or datetime.now()
    settings = {"RELATIVE_BASE": now, "PREFER_DATES_FROM": "past"}
    normalized_query = _normalize_spoken_hours(query)
    try:
        matches = search_dates(normalized_query, languages=["ro", "en"], settings=settings)
    except Exception:  # noqa: BLE001 — a parsing edge case must not break retrieval
        return None
    if not matches:
        return None

    matched_text, center = matches[0]

    if not _is_plausible_year(center.year, now):
        # Defense-in-depth: _normalize_spoken_hours fixes the specific
        # colon-less-hour misparse confirmed live, but dateparser can in
        # principle misread some other bare number in the phrase as a
        # 2-digit year the same way, in a phrasing this fix doesn't cover.
        # PREFER_DATES_FROM="past" only ever legitimately rolls a match back
        # ONE calendar year (see test_future_date_resolves_to_past_year) —
        # for a daily-use personal assistant, a genuine reference further
        # back than that is rare enough that treating it as a misparse is a
        # reasonable, defensible tradeoff. Retry once with a trailing hour
        # phrase stripped (in case that's the culprit); give up rather than
        # silently return a wrong-decade window if the retry doesn't help.
        stripped_query = _TRAILING_HOUR_PHRASE_RE.sub("", normalized_query).strip()
        retry_matches = None
        if stripped_query and stripped_query != normalized_query:
            try:
                retry_matches = search_dates(stripped_query, languages=["ro", "en"], settings=settings)
            except Exception:  # noqa: BLE001 — same rationale as the primary call above
                retry_matches = None
        if not retry_matches:
            return None
        matched_text, center = retry_matches[0]
        if not _is_plausible_year(center.year, now):
            return None

    has_time = any(marker in matched_text.lower() for marker in _TIME_MARKERS)
    if has_time:
        delta = timedelta(hours=1)
    else:
        delta = timedelta(hours=12)
        center = center.replace(hour=12, minute=0, second=0, microsecond=0)

    after = (center - delta).timestamp()
    before = (center + delta).timestamp()
    return after, before
