"""memory.temporal_route — parse GLiNER-extracted date/time entity text to an interval.

GLiNER extracts entity text such as "4 iulie" (DATE) or "07:00" (TIME).
This module converts those strings to a (after_ts, before_ts) Unix timestamp
interval using a simple token walk + Romanian month dictionary — no regex,
no external parser.  The entity boundary was already found by GLiNER.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

_MONTHS_RO: dict[str, int] = {
    "ianuarie": 1, "ian": 1,
    "februarie": 2, "feb": 2,
    "martie": 3, "mar": 3,
    "aprilie": 4, "apr": 4,
    "mai": 5,
    "iunie": 6, "iun": 6,
    "iulie": 7, "iul": 7,
    "august": 8, "aug": 8,
    "septembrie": 9, "sep": 9, "sept": 9,
    "octombrie": 10, "oct": 10,
    "noiembrie": 11, "nov": 11,
    "decembrie": 12, "dec": 12,
}

_TEMPORAL_TYPES = {"date", "time"}


def _parse_tokens(
    tokens: list[str],
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    """Walk tokens and classify each as day / month / year / time.

    Token rules (mutually exclusive, in order):
      - contains ':' → HH:MM time component
      - lowercased form in _MONTHS_RO → month name
      - pure digits, > 1000 → year
      - pure digits, 1–31 → day
    """
    day = month = year = hour = minute = None
    for token in tokens:
        t = token.lower().strip(".,;")
        if ":" in t:
            parts = t.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                hour, minute = int(parts[0]), int(parts[1])
        elif t in _MONTHS_RO:
            month = _MONTHS_RO[t]
        elif t.isdigit():
            n = int(t)
            if n > 1000:
                year = n
            elif 1 <= n <= 31:
                day = n
    return day, month, year, hour, minute


def parse_interval(
    entities: list[str],
    entity_types: list[str],
) -> tuple[float, float] | None:
    """Return (after_ts, before_ts) from GLiNER date/time entity texts, or None.

    Strategy:
    1. Prefer entities whose label is "date" or "time"; fall back to all texts
       (catches dates GLiNER labelled as "event").
    2. Walk tokens to extract day, month, optional year/time.
    3. Window: ±1 h when a time component is present, ±12 h (full day) otherwise.
    4. If the resulting interval is > 1 day in the future, retry with year − 1.
    """
    preferred = [e for e, t in zip(entities, entity_types) if t.lower() in _TEMPORAL_TYPES]
    tokens = " ".join(preferred or entities).split()

    day, month, year, hour, minute = _parse_tokens(tokens)
    if day is None or month is None:
        return None
    if year is None:
        year = datetime.now().year

    try:
        if hour is not None and minute is not None:
            center = datetime(year, month, day, hour, minute)
            delta = timedelta(hours=1)
        else:
            center = datetime(year, month, day, 12, 0)
            delta = timedelta(hours=12)
    except ValueError:
        return None

    now = time.time()
    after = (center - delta).timestamp()
    before = (center + delta).timestamp()

    if after > now + 86_400:  # date > tomorrow → probably meant last year
        try:
            center = center.replace(year=year - 1)
            after = (center - delta).timestamp()
            before = (center + delta).timestamp()
        except ValueError:
            return None

    return after, before
