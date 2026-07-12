"""memory.date_format — language-neutral timestamp/duration formatting.

Absolute timestamps render as ISO-8601 (zero natural-language content).
Relative durations render as short English phrases used as protocol
scaffolding — the same role as "Current time" / "Known facts" elsewhere in
the assembled prompt — not as a user-facing language choice. The LLM
translates these into whatever language the conversation is actually in,
the same trivial way it translates any other English scaffold label; Python
only owns the arithmetic (which the LLM is unreliable at), never the
target-language phrasing. A prior version hardcoded Romanian phrasing here,
which broke down the moment the conversation was in a different language —
this module now hardcodes zero locale-specific vocabulary.
"""
from __future__ import annotations

from datetime import datetime

from memory.config_extra import RELATIVE_HORIZON_SECONDS


def format_iso(ts: float) -> str:
    """Unix timestamp -> timezone-aware ISO-8601 string, e.g. '2026-07-05T15:23:00+03:00'."""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def prefix_with_date(content: str, ts: float) -> str:
    """Prepend an ISO-8601 timestamp header to memory content."""
    return f"[{format_iso(ts)}] {content}"


def format_duration(seconds: float) -> str:
    """Elapsed seconds -> a short English duration string, e.g. '20 min', '1 hour', '3 days'."""
    seconds = max(seconds, 0)
    if seconds < 60:
        return "under a minute"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = int(seconds // 3600)
    if hours < 24:
        return "1 hour" if hours == 1 else f"{hours} hours"
    days = int(seconds // 86400)
    return "1 day" if days == 1 else f"{days} days"


def format_relative(ts: float, now: float) -> str:
    """Unix timestamp -> a short relative-time phrase anchored at now, e.g. '20 min ago'.

    Falls back to format_iso beyond RELATIVE_HORIZON_SECONDS, where a
    relative phrase stops being useful.
    """
    delta = now - ts
    if delta < 60:
        return "just now"
    if delta >= RELATIVE_HORIZON_SECONDS:
        return format_iso(ts)
    return f"{format_duration(delta)} ago"
