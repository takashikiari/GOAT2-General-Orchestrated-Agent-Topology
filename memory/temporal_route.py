"""memory.temporal_route — convert GLiNER date/time entities to a search interval.

GLiNER extracts DATE and TIME entities from the query text.  This module
converts those entity strings (e.g. "4 iulie", "07:00") into a Unix timestamp
interval (after, before) suitable for search_episodic's after/before filter.

No external parser is used — a minimal regex + Romanian month lookup handles
the common natural-language date patterns.  If parsing fails, returns None.
"""
from __future__ import annotations

import re
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

_DATE_PAT = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_MONTHS_RO) + r")\b(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
_TIME_PAT = re.compile(r"\b(\d{1,2}):(\d{2})\b")

_TEMPORAL_TYPES = {"date", "time"}


def parse_interval(
    entities: list[str],
    entity_types: list[str],
) -> tuple[float, float] | None:
    """Return (after_ts, before_ts) or None if no parseable date found.

    Strategy:
    1. Prefer entities whose label is "date" or "time".
    2. Fallback: scan all entity texts for a date pattern (catches "event"-labelled dates).
    3. If a time component is present, window is ±1 h; otherwise full day ±12 h.
    4. If the parsed date is > 1 day in the future, retry with previous year.
    """
    # Prefer date/time labelled entities; fall back to scanning all texts
    preferred = [e for e, t in zip(entities, entity_types) if t.lower() in _TEMPORAL_TYPES]
    combined = " ".join(preferred or entities)

    dm = _DATE_PAT.search(combined)
    if not dm:
        return None

    day = int(dm.group(1))
    month = _MONTHS_RO.get(dm.group(2).lower(), 0)
    if not month:
        return None
    year = int(dm.group(3)) if dm.group(3) else datetime.now().year

    tm = _TIME_PAT.search(combined)
    try:
        if tm:
            center = datetime(year, month, day, int(tm.group(1)), int(tm.group(2)))
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
