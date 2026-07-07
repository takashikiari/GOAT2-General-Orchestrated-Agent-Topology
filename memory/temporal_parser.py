"""memory.temporal_parser — grammatical date-range extraction for the prefetch daemon.

A robust date parser (``dateparser``, multilingual RO+EN) — NOT a keyword list.
It extracts a real time range from the query and returns it only when the range
ends strictly in the past (a completed period). 'azi'/'today' (whose day-range
ends tonight) therefore yields ``None``, while 'ieri'/'yesterday' or a past
absolute date yields that day's range. ``memory.query_classifier`` uses this to
score the temporal mechanism: 1.0 when a completed-past range is found, else 0.

The "completed-past" rule is what keeps 'azi' from false-triggering when used
non-temporally (e.g. "când ți-am spus salut azi?"): 'azi' parses to today, whose
day-range end is in the future, so no range is returned and the temporal
mechanism is skipped — exactly the behaviour required for live test #9.
"""
from __future__ import annotations

import time

import dateparser  # noqa: F401 — registers languages; kept for clarity
from dateparser.search import search_dates

# STRICT_PARSING rejects ambiguous month-only tokens (RO "mai" = adverb
# "still/more" vs month "May") that would otherwise contaminate "azi" and
# false-trigger temporal. Languages: RO primary, EN secondary.
_SETTINGS_STRICT = {"RETURN_AS_TIMEZONE_AWARE": False, "STRICT_PARSING": True}
# Loose pass for "5 iulie"-style dates (day+month, no year). PREFER_DATES_FROM
# biases toward past when year is missing. Only used when strict finds nothing,
# and only matched strings containing a digit are accepted — this blocks the
# "mai" adverb false-positive ("mai vorbim" has no digit → rejected).
_SETTINGS_LOOSE = {
    "RETURN_AS_TIMEZONE_AWARE": False,
    "STRICT_PARSING": False,
    "PREFER_DATES_FROM": "past",
}
_LANGS = ["ro", "en"]


def _search(query: str) -> list:
    """Two-pass date search: strict first, loose-with-digit-guard second."""
    try:
        found = search_dates(query, languages=_LANGS, settings=_SETTINGS_STRICT)
        if found:
            return found
        # Loose pass: accept only if the matched substring contains a digit
        # (filters out month-name adverbs like RO "mai" that have no day number).
        found_loose = search_dates(query, languages=_LANGS, settings=_SETTINGS_LOOSE)
        if found_loose:
            return [(text, dt) for text, dt in found_loose if any(c.isdigit() for c in text)]
    except Exception:                            # noqa: BLE001
        pass
    return []


def extract_temporal_range(
    query: str, now: float | None = None,
) -> tuple[float, float] | None:
    """Parse a completed-past time range; ``(after, before)`` unix ts or ``None``.

    Uses dateparser's grammatical search (no word list). With one parsed date
    the range is that calendar day; with two or more, the span from the earliest
    to the latest. Returned only if the range ends strictly in the past
    (``before < now``), so present-day references ('azi'/'today') yield ``None``.

    Args:
        query: The user message to scan for a time reference.
        now: Reference "now" unix ts (defaults to ``time.time()``); injectable
            for deterministic tests.
    Returns:
        ``(after, before)`` unix timestamps, or ``None`` when no completed-past
        range was found.
    """
    now = now if now is not None else time.time()
    found = _search(query)
    if not found:
        return None
    dates = [dt for _, dt in found]
    after_dt = min(dates).replace(hour=0, minute=0, second=0, microsecond=0)
    before_dt = max(dates).replace(hour=23, minute=59, second=59, microsecond=0)
    after = after_dt.timestamp()
    before = before_dt.timestamp()
    if before >= now:       # range reaches into the present/future → not completed-past
        return None
    return after, before