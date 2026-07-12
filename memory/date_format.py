"""memory.date_format — Romanian-locale date prefix for L3 content.

Prepending a human-readable Romanian date to stored content lets BM25 and
MiniLM both surface memories by date without any query-time parsing:
"[5 iulie 2026, 15:23] ..." is lexically matchable by BM25 ("iulie") and
semantically closer to "pe 5 iulie" queries than raw conversation text.
"""
from __future__ import annotations

from datetime import datetime

from memory.config_extra import RELATIVE_HORIZON_SECONDS

_RO_MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]


def format_ro_date(ts: float) -> str:
    """Unix timestamp → Romanian date string, e.g. '5 iulie 2026, 15:23'."""
    dt = datetime.fromtimestamp(ts)
    return f"{dt.day} {_RO_MONTHS[dt.month - 1]} {dt.year}, {dt.strftime('%H:%M')}"


def prefix_with_date(content: str, ts: float) -> str:
    """Prepend a Romanian date header to memory content."""
    return f"[{format_ro_date(ts)}] {content}"


def format_duration_ro(seconds: float) -> str:
    """Elapsed seconds -> Romanian duration string, e.g. '20 min', '1 oră', '3 zile'."""
    seconds = max(seconds, 0)
    if seconds < 60:
        return "sub un minut"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = int(seconds // 3600)
    if hours < 24:
        return "1 oră" if hours == 1 else f"{hours} ore"
    days = int(seconds // 86400)
    return "1 zi" if days == 1 else f"{days} zile"


def format_relative_ro(ts: float, now: float) -> str:
    """Unix timestamp -> Romanian relative-time string anchored at now, e.g. 'acum 20 min'.

    Falls back to format_ro_date beyond RELATIVE_HORIZON_SECONDS, where a
    relative phrase stops being useful.
    """
    delta = now - ts
    if delta < 60:
        return "chiar acum"
    if delta >= RELATIVE_HORIZON_SECONDS:
        return format_ro_date(ts)
    return f"acum {format_duration_ro(delta)}"
