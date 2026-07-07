"""memory.date_format — Romanian-locale date prefix for L3 content.

Prepending a human-readable Romanian date to stored content lets BM25 and
MiniLM both surface memories by date without any query-time parsing:
"[5 iulie 2026, 15:23] ..." is lexically matchable by BM25 ("iulie") and
semantically closer to "pe 5 iulie" queries than raw conversation text.
"""
from __future__ import annotations

from datetime import datetime

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
