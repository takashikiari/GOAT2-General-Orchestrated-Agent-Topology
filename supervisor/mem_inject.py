"""Fan-out recall across all three memory tiers and concurrent info extraction per turn."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

from supervisor.info_extract import maybe_store_info

__all__ = ["mem_turn", "recall_context"]

_LIMIT: Final[int] = 20


async def recall_context(mm: MemoryManager | None, query: str) -> str:
    """Fan-out recall across WORKING + EPISODIC + LONG_TERM; returns '[Memory]\n- …' or ''."""
    if mm is None:
        return ""
    try:
        hits = await mm.recall(SESSION_ROLE, query, limit=_LIMIT)
    except Exception:
        return ""
    lines = [h.content.strip() for h in hits if h.content.strip()]
    return ("[Memory]\n" + "\n".join(f"- {ln}" for ln in lines)) if lines else ""


async def mem_turn(mm: MemoryManager | None, intent: str) -> str:
    """Recall memory and store any new facts from intent concurrently; returns [Memory] block."""
    ctx, _ = await asyncio.gather(
        recall_context(mm, intent),
        maybe_store_info(mm, intent),
    )
    return ctx
