"""memory.temporal_route — parse a date/time expression embedded in a query.

Uses dateparser.search.search_dates to find and parse date/time expressions
directly from the raw query text — absolute ("4 iulie") and relative ("ieri",
"acum 2 ore") alike, natively in Romanian. Replaces a hand-rolled token-walk
parser that required an explicit day+month token pair and only ever saw
GLiNER-extracted DATE/TIME entity text. Confirmed on real data (2026-07-08):
GLiNER detects zero entities for relative Romanian expressions like "ieri" or
"acum 2 ore", so the old GLiNER-gated pipeline never routed those queries
temporally at all, regardless of parser quality.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from dateparser.search import search_dates

__all__ = ["parse_interval"]

# Words/symbols in the matched span implying a specific hour was given ->
# narrow ±1h window. Their absence means a day-level match -> ±12h window
# centered on noon (mirrors the old parser's date-only behavior).
_TIME_MARKERS = (":", "oră", "ora", "orei", "ore", "minut")


def parse_interval(query: str, now: datetime | None = None) -> tuple[float, float] | None:
    """Find a date/time expression in ``query``; return an (after_ts, before_ts) window.

    Returns ``None`` when no date/time expression is found. ``now`` anchors
    relative expressions ("ieri", "acum 2 ore") and biases ambiguous dates
    toward the past (recall queries are about past events) — defaults to the
    real current time; tests pass a fixed value.
    """
    now = now or datetime.now()
    settings = {"RELATIVE_BASE": now, "PREFER_DATES_FROM": "past"}
    try:
        matches = search_dates(query, languages=["ro", "en"], settings=settings)
    except Exception:  # noqa: BLE001 — a parsing edge case must not break retrieval
        return None
    if not matches:
        return None

    matched_text, center = matches[0]
    has_time = any(marker in matched_text.lower() for marker in _TIME_MARKERS)
    if has_time:
        delta = timedelta(hours=1)
    else:
        delta = timedelta(hours=12)
        center = center.replace(hour=12, minute=0, second=0, microsecond=0)

    after = (center - delta).timestamp()
    before = (center + delta).timestamp()
    return after, before
