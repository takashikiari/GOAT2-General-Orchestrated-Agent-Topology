"""filter_by_time / resolve_range — pure post-filter and human-range parser."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.shared.types import MemoryEntry

__all__ = ["filter_by_time", "resolve_range"]

log = logging.getLogger("goat2.memory.temporal")


def filter_by_time(
    entries: list[MemoryEntry],
    start_ts: float | None,
    end_ts: float | None,
) -> list[MemoryEntry]:
    """Post-filter entries by created_at_ts epoch.

    Entries where created_at_ts is 0 or absent are excluded when any filter is active
    — never invent or assume timestamps for legacy/migrated records.
    """
    if start_ts is None and end_ts is None:
        return entries
    result: list[MemoryEntry] = []
    for e in entries:
        ts = float(e.metadata.get("created_at_ts") or 0)
        if ts == 0.0:
            continue  # unknown timestamp: exclude rather than guess
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts > end_ts:
            continue
        result.append(e)
    log.debug("filter_by_time: kept=%d (from %d)", len(result), len(entries))
    return result


def resolve_range(
    start_expr: str | None,
    end_expr: str | None,
) -> tuple[float | None, float | None]:
    """Resolve human-readable or ISO expressions to (start_epoch, end_epoch).

    When start_expr encodes a compound range (e.g. "yesterday morning"),
    its implied end overrides end_expr.
    """
    from memory.temporal.time_parser import parse_time_range

    if start_expr:
        s_start, s_end = parse_time_range(start_expr)
        if end_expr:
            e_start, _ = parse_time_range(end_expr)
            return s_start, e_start
        return s_start, s_end  # compound range like "yesterday morning"

    if end_expr:
        _, e_end = parse_time_range(end_expr)
        return None, e_end

    return None, None
