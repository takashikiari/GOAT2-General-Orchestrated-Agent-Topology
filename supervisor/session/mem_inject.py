"""Fan-out recall across all three memory tiers and concurrent info extraction per turn.

REGISTRY INJECTION (PHASE 4):
=============================
mem_turn() now requires `registry` parameter.
Passed to maybe_store_info() for settings access.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import Registry

from supervisor.behavior.info_extract import maybe_store_info

__all__ = ["mem_turn", "recall_context", "working_memory_block"]

_LIMIT: Final[int] = 5
_WM_LIMIT: Final[int] = 50
log = logging.getLogger("goat2.supervisor.session")


def _fmt_ts(record: dict) -> str:
    """Render a record's creation time as 'YYYY-MM-DD HH:MM' for display."""
    ts = record.get("created_at_ts")
    if ts:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return str(record.get("created_at") or "")[:16]


async def working_memory_block(mm: MemoryManager | None) -> str:
    """Build a '[Working Memory]' block listing ALL working entries for the session.

    Loads every entry (up to _WM_LIMIT) directly from the working backend — no
    semantic-similarity filtering — so GOAT has complete session awareness. Each
    line is '- <key> (<timestamp>): <content>', oldest first. Includes ``dag:*``
    coordination entries. Returns '' on any failure or when empty.
    """
    if mm is None:
        return ""
    try:
        backend = mm.working.backend
        records: list[dict] = []
        for k in await backend.keys(SESSION_ROLE):
            rec = await backend.get(SESSION_ROLE, k)
            if rec:
                records.append(rec)
        records.sort(key=lambda r: float(r.get("created_at_ts") or 0.0))
        if not records:
            return ""
        lines = [
            f"- {r.get('key', '?')} ({_fmt_ts(r)}): {(r.get('content') or '').strip().replace(chr(10), ' ')}"
            for r in records[:_WM_LIMIT]
        ]
        log.debug("working_memory_block: %d entries", len(lines))
        return "[Working Memory]\n" + "\n".join(lines)
    except Exception as exc:
        log.debug("working_memory_block failed: %s", exc)
        return ""


async def recall_context(mm: MemoryManager | None, query: str) -> str:
    """Return the cross-tier '[Memory]' fan-out PLUS the full '[Working Memory]' block.

    The fan-out (WORKING+EPISODIC+LONG_TERM semantic recall) is preserved for
    relevance; the working-memory block is appended unfiltered so GOAT sees every
    live session entry. Degrades to whichever block is available on error.
    """
    if mm is None:
        return "[Memory: UNAVAILABLE]"
    try:
        hits = await mm.recall(SESSION_ROLE, query, limit=_LIMIT)
        lines = [h.content.strip() for h in hits if h.content.strip()]
        mem_block = ("[Memory]\n" + "\n".join(f"- {ln}" for ln in lines)) if lines else ""
    except Exception as exc:
        log.error("recall_context fan-out failed: %s: %s", type(exc).__name__, exc)
        mem_block = ""
    wm_block = await working_memory_block(mm)
    blocks = [b for b in (mem_block, wm_block) if b]
    return "\n".join(blocks) if blocks else "[Memory: UNAVAILABLE]"


async def mem_turn(
    mm: MemoryManager | None,
    intent: str,
    registry: "Registry",
) -> str:
    """
    Recall memory and store any new facts from intent concurrently; returns [Memory] block.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Passed to maybe_store_info() for settings access.
    """
    ctx, _ = await asyncio.gather(
        recall_context(mm, intent),
        maybe_store_info(mm, intent, registry),
    )
    return ctx
