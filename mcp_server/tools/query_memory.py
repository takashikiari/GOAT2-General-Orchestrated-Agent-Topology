"""Memory-tier query tools — inspect the three tiers
(working / episodic / long-term) without writing anything.

READ-ONLY: every method called on the memory manager /
working layer / episodic / Letta client is a pure read
(``list``, ``search``, ``count``, ``health``, ``recall``,
``keys``, ``get``, ``read_last_write``). No ``store``,
``delete``, ``clear``, ``flush``, or ``set_block`` is ever
invoked. The MCP server is safe to run concurrently with
``telegram_bot.py``.

USAGE:
    from mcp_server.tools.query_memory import (
        get_memory_snapshot, get_recent_entries, register,
    )
    snap = await get_memory_snapshot()
    items = await get_recent_entries("working", limit=10)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Final

from mcp_server._registry import get_registry

log = logging.getLogger("goat2.mcp_server.tools.query_memory")

__all__ = ["get_memory_snapshot", "get_recent_entries", "get_memory_metrics", "register"]


# Tier names the tools accept. ``long_term`` matches the
# in-code attribute name on ``MemoryManager``; the wire
# vocabulary mirrors what ``query_logs`` / ``query_config``
# expect.
_VALID_TIERS: Final[tuple[str, ...]] = ("working", "episodic", "long_term")

# Tier role to use when listing entries. The working layer
# is keyed by ``agent_role`` — for a generic snapshot we use
# ``SESSION_ROLE`` (set by ``config/roles``), which is the
# role telegram_bot writes under for the user session.
_DEFAULT_ROLE: Final[str] = "user_session"


async def get_memory_snapshot() -> dict[str, Any]:
    """Return per-tier counts and last-write timestamps.

    Returns:
        A dict with one entry per tier:
            ``counts``        — entry count per tier
            ``last_write_ts`` — float seconds-since-epoch
                                 (or ``None`` when never written)
            ``last_write_iso``— ISO-8601 string for human display
            ``health``        — ``True`` / ``False`` per tier
        ``errors`` lists per-tier exceptions that occurred
        while querying (so the snapshot is still useful when
        one tier is down).
    """
    out: dict[str, Any] = {
        "counts":         {},
        "last_write_ts":  {},
        "last_write_iso": {},
        "health":         {},
        "errors":         {},
    }
    try:
        from memory.shared.last_write import read_last_write
        registry = get_registry()
        mm = registry.memory_manager
        for tier in _VALID_TIERS:
            try:
                ts = await read_last_write(tier)
            except Exception as exc:  # noqa: BLE001
                out["errors"][tier] = f"read_last_write: {exc}"
                ts = None
            out["last_write_ts"][tier] = ts
            if isinstance(ts, (int, float)) and ts > 0:
                try:
                    out["last_write_iso"][tier] = (
                        datetime.fromtimestamp(float(ts), tz=timezone.utc)
                        .isoformat()
                    )
                except (OverflowError, OSError, ValueError):
                    out["last_write_iso"][tier] = None
            else:
                out["last_write_iso"][tier] = None
            # Counts + health (best-effort per tier).
            try:
                if tier == "working":
                    out["counts"][tier] = await mm.working.count(_DEFAULT_ROLE)
                    out["health"][tier] = bool(await mm.working.health())
                elif tier == "episodic":
                    out["counts"][tier] = await mm.episodic.count(_DEFAULT_ROLE)
                    out["health"][tier] = bool(await mm.episodic.health())
                elif tier == "long_term":
                    out["counts"][tier] = -1  # Letta doesn't have a single count
                    out["health"][tier] = bool(await mm.long_term.health())
            except Exception as exc:  # noqa: BLE001
                out["errors"][tier] = out["errors"].get(tier, "") + f"; count/health: {exc}"
    except Exception as exc:  # noqa: BLE001 — registry unavailable
        out["errors"]["registry"] = f"{exc}"
    return out


async def get_recent_entries(tier: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent entries from ``tier`` with full metadata.

    Args:
        tier: One of ``"working"``, ``"episodic"``, ``"long_term"``.
        limit: Maximum entries to return. Default 10; capped at 100.

    Returns:
        A list of dicts. Each dict has ``key``, ``content``,
        ``created_at_ts`` (float seconds since epoch),
        ``created_at`` (ISO string), ``source``, and the
        freshness label (``"FRESH" | "RECENT" | "OLD"``) when
        ``created_at_ts`` is parseable. ``metadata`` is included
        when present on the underlying record. Empty list on
        any failure.
    """
    tier_lc = (tier or "").lower()
    if tier_lc not in _VALID_TIERS:
        log.warning("get_recent_entries: unknown tier %r", tier)
        return []
    cap = max(1, min(int(limit or 10), 100))
    try:
        registry = get_registry()
        mm = registry.memory_manager
        if tier_lc == "working":
            entries = await mm.working.list(_DEFAULT_ROLE, limit=cap)
        elif tier_lc == "episodic":
            entries = await mm.episodic.list(_DEFAULT_ROLE, limit=cap)
        else:
            # Long-term: list core-memory blocks via the
            # dedicated long_term.list(). Returned shape is
            # different from working/episodic — adapt.
            raw = await mm.long_term.list(_DEFAULT_ROLE, limit=cap)
            return _adapt_long_term_list(raw, cap)
    except Exception as exc:  # noqa: BLE001
        log.warning("get_recent_entries(%s): failed: %s", tier_lc, exc)
        return []
    return _adapt_memory_entries(entries)


def _adapt_memory_entries(entries: list) -> list[dict[str, Any]]:
    """Convert working / episodic ``MemoryEntry`` objects to dicts.

    Adds the freshness label via ``supervisor.mechanisms.freshness``
    so the MCP client sees the same label GOAT itself uses
    when rendering the working-memory block.
    """
    from supervisor.mechanisms.freshness import score_freshness
    now = datetime.now(tz=timezone.utc).timestamp()
    out: list[dict[str, Any]] = []
    for e in entries:
        try:
            meta = getattr(e, "metadata", {}) or {}
            created_at_ts = float(meta.get("created_at_ts") or 0)
        except (TypeError, ValueError):
            created_at_ts = 0.0
        record = {
            "key":            getattr(e, "key", ""),
            "content":        getattr(e, "content", ""),
            "created_at":     getattr(e, "created_at", ""),
            "created_at_ts":  created_at_ts,
            "source":         getattr(e, "source", ""),
            "freshness":      score_freshness({"created_at_ts": created_at_ts}, now),
        }
        if meta:
            record["metadata"] = dict(meta)
        out.append(record)
    return out


async def get_memory_metrics() -> dict[str, Any]:
    """Return the in-process per-tier metrics counter snapshot.

    Counters are bumped from the supervisor's per-turn flow and
    the ``MemoryDaemon``'s per-sweep path (see
    ``memory.memory_metrics.counters``). Useful for answering
    "is the tier promotion actually running?" and "how many
    episodic hits did the LLM see this session?" without
    grepping logs.

    Returns:
        A dict with one entry per counter key (dotted event
        names like ``memory.working.write``,
        ``memory.daemon.tier1_promote``). Empty dict when
        no counters have fired yet.
    """
    try:
        from memory.memory_metrics import snapshot as _snapshot
        return _snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _adapt_long_term_list(raw, limit: int) -> list[dict[str, Any]]:
    """Adapt Letta's ``list()`` output to the same dict shape."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        out.append({
            "key":           str(item.get("label") or item.get("key") or ""),
            "content":       str(item.get("content") or ""),
            "created_at":    str(item.get("created_at") or ""),
            "created_at_ts": float(item.get("created_at_ts") or 0.0),
            "source":        "long_term",
            "freshness":     "OLD",  # Letta blocks aren't time-scored by GOAT
        })
    return out


# ── MCP wiring ────────────────────────────────────────────────

def register(server) -> None:
    """Register the two memory tools on an MCP ``Server``."""
    @server.tool(
        name="get_memory_snapshot",
        description=(
            "Return per-tier (working / episodic / long_term) counts, last-write timestamps, "
            "and health flags. Useful as a first diagnostic when GOAT seems unresponsive."
        ),
    )
    async def _get_memory_snapshot() -> dict[str, Any]:
        return await get_memory_snapshot()

    @server.tool(
        name="get_recent_entries",
        description=(
            "Return recent entries from a memory tier ('working' | 'episodic' | 'long_term'). "
            "Each entry includes key, content, created_at_ts, freshness label (FRESH/RECENT/OLD), "
            "and metadata. READ-ONLY."
        ),
    )
    async def _get_recent_entries(tier: str, limit: int = 10) -> list[dict[str, Any]]:
        return await get_recent_entries(tier=tier, limit=limit)

    @server.tool(
        name="get_memory_metrics",
        description=(
            "Return the in-process per-tier metrics counter snapshot: "
            "memory.working.write / flush, memory.episodic.hit, "
            "memory.daemon.tier1_promote / tier2_slide, "
            "memory.session.flush / daemon_start. Counters are "
            "in-memory (reset on process restart). Empty dict when "
            "no activity yet."
        ),
    )
    async def _get_memory_metrics() -> dict[str, Any]:
        return await get_memory_metrics()