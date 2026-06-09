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
    from memory.memory_manager import MemoryManager
    from config.registry import Registry

from supervisor.info_extract import maybe_store_info

__all__ = ["mem_turn", "recall_context"]

_LIMIT: Final[int] = 5
log = logging.getLogger("goat2.mem_inject")


async def recall_context(mm: MemoryManager | None, query: str) -> str:
    """Fan-out recall across WORKING + EPISODIC + LONG_TERM; returns '[Memory]\n- …' or ''."""
    if mm is None:
        return "[Memory: UNAVAILABLE]"
    try:
        hits = await mm.recall(SESSION_ROLE, query, limit=_LIMIT)
    except Exception as exc:
        log.error("recall_context failed: %s: %s", type(exc).__name__, exc)
        return "[Memory: UNAVAILABLE]"
    lines = [h.content.strip() for h in hits if h.content.strip()]
    return ("[Memory]\n" + "\n".join(f"- {ln}" for ln in lines)) if lines else ""


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
