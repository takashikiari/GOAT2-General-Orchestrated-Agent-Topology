from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = ["parse_time_range"]

log = logging.getLogger("goat2.memory.temporal")

_TZ = ZoneInfo("Europe/Bucharest")
_UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(_TZ)


def _today() -> datetime:
    return _now().replace(hour=0, minute=0, second=0, microsecond=0)


def _ts(dt: datetime) -> float:
    return dt.astimezone(_UTC).timestamp()


def parse_time_range(expr: str) -> tuple[float | None, float | None]:
    """Parse a time expression to (start_epoch, end_epoch).

    Returns (epoch, None) for point-in-time; (None, None) if unrecognised.
    Default timezone: Europe/Bucharest. Stored timestamps are UTC epoch floats.
    """
    if not expr:
        return None, None
    low = expr.strip().lower()
    log.debug("parse_time_range: expr=%r", low)
    now = _now()
    today = _today()
    yest = today - timedelta(days=1)

    if low == "today":
        return _ts(today), _ts(now)
    if low == "yesterday":
        return _ts(yest), _ts(today)
    if low in ("yesterday morning", "dimineata de ieri"):
        return _ts(yest.replace(hour=6)), _ts(yest.replace(hour=12))
    if low in ("last night", "ieri seara", "noaptea trecuta"):
        end = yest.replace(hour=23, minute=59, second=59)
        return _ts(yest.replace(hour=18)), _ts(end)

    m = re.match(r"last\s+(\d+)\s*h(?:ours?)?$", low)
    if m:
        return _ts(now - timedelta(hours=int(m.group(1)))), _ts(now)

    m = re.match(r"last\s+(\d+)\s*days?$", low)
    if m:
        return _ts(now - timedelta(days=int(m.group(1)))), _ts(now)

    m = re.match(r"last\s+(\d+)\s*weeks?$", low)
    if m:
        return _ts(now - timedelta(weeks=int(m.group(1)))), _ts(now)

    # ISO 8601 point-in-time (treat as local TZ if no tz info)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(expr.strip(), fmt).replace(tzinfo=_TZ)
            return _ts(dt), None
        except ValueError:
            pass

    # Already a numeric epoch?
    try:
        return float(expr), None
    except (ValueError, TypeError):
        pass

    return None, None
