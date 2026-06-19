"""Memory injection — assemble the working-memory context block
for the LLM call. Composition of the ``mechanisms/`` primitives.

USAGE:
    from supervisor.session.mem_inject import mem_turn, recall_context

    ctx = await mem_turn(mm, intent, registry)
    # → "[Memory]\\n...\\n[Working Memory]\\n- [FRESH][CONV] turn:abc: hi"

PIECES:
  - ``recall_context``: cross-tier fan-out (working + episodic +
    long-term) for the ``[Memory]`` block.
  - ``working_memory_block``: per-key working-memory entries
    with the freshness × source label, via
    ``mechanisms.context_builder``.
  - ``mem_turn``: convenience wrapper that fans out both,
    filtered by the registry's view of memory.

The actual semantic-search call lives in
``MemoryManager.recall``; the working-memory fetch lives in
``WorkingMemoryLayer.backend.list``. We orchestrate them here
and render the result through the mechanisms so the output
format stays in one place.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from memory.shared import MemoryManager

from config.roles import SESSION_ROLE
from supervisor.mechanisms.context_builder import build_context
from supervisor.mechanisms.namespace import is_dag_key

__all__ = ["mem_turn", "recall_context", "working_memory_block"]

# Tunables — kept module-local so the file stays under 260.
_RECALL_LIMIT:        Final[int] = 5
_WM_LIMIT:            Final[int] = 50
_NO_MEMORY_FALLBACK:  Final[str] = "[Memory: UNAVAILABLE]"

log = logging.getLogger("goat2.supervisor.session.mem_inject")


async def _list_working(mm: "MemoryManager") -> list[dict]:
    """Fetch recent working-memory records for the SESSION_ROLE."""
    try:
        backend = mm.working.backend
        keys = await backend.keys(SESSION_ROLE)
        records: list[dict] = []
        for k in keys[:_WM_LIMIT]:
            rec = await backend.get(SESSION_ROLE, k)
            if rec:
                records.append(rec)
        return records
    except Exception as exc:  # noqa: BLE001 — never block on memory
        log.debug("_list_working failed: %s", exc)
        return []


async def _filter_dag(records: list[dict], include_dag: bool) -> list[dict]:
    """Drop ``dag:*`` entries unless ``include_dag`` is set."""
    if include_dag:
        return list(records)
    return [r for r in records if not is_dag_key(r.get("key", ""))]


async def working_memory_block(
    mm: "MemoryManager | None",
    *,
    include_dag: bool = False,
) -> str:
    """Render the ``[Working Memory]`` block (freshness × source scored).

    Args:
        mm: MemoryManager (or None → ``""``).
        include_dag: When False (default), ``dag:*`` entries are
            excluded so DAG coordination never leaks into the
            conversational prompt.

    Returns:
        The rendered block (string starting with
        ``"[Working Memory]\\n"``), or ``""`` on failure or when
        no entries survive filtering.
    """
    if mm is None:
        return ""
    try:
        records = await _list_working(mm)
        records = await _filter_dag(records, include_dag)
        return build_context(records, intent="", now=time.time())
    except Exception as exc:  # noqa: BLE001
        log.debug("working_memory_block failed: %s", exc)
        return ""


async def recall_context(
    mm: "MemoryManager | None",
    query: str,
    *,
    include_dag: bool = False,
) -> str:
    """Return cross-tier ``[Memory]`` + working-memory block.

    Args:
        mm: MemoryManager (or None → ``[Memory: UNAVAILABLE]``).
        query: The raw user intent (drives the semantic recall).
        include_dag: When True, ``dag:*`` entries are included in
            the working-memory block (used by the few callers that
            need the unfiltered view).

    Returns:
        Concatenated ``[Memory]`` and ``[Working Memory]`` blocks,
        or the UNAVAILABLE marker when both fail.
    """
    if mm is None:
        return _NO_MEMORY_FALLBACK
    mem_block = ""
    wm_block  = ""
    try:
        hits = await mm.recall(SESSION_ROLE, query, limit=_RECALL_LIMIT)
        lines = [h.content.strip() for h in hits if h.content.strip()]
        if lines:
            mem_block = "[Memory]\n" + "\n".join(f"- {ln}" for ln in lines)
    except Exception as exc:  # noqa: BLE001
        log.debug("recall_context fan-out failed: %s", exc)
    try:
        wm_block = await working_memory_block(mm, include_dag=include_dag)
    except Exception as exc:  # noqa: BLE001
        log.debug("recall_context wm failed: %s", exc)
    blocks = [b for b in (mem_block, wm_block) if b]
    return "\n".join(blocks) if blocks else _NO_MEMORY_FALLBACK


async def mem_turn(
    mm: "MemoryManager | None",
    intent: str,
) -> str:
    """Convenience: run ``recall_context`` for this turn.

    Args:
        mm: MemoryManager (or None).
        intent: Raw user intent.

    Returns:
        The rendered memory-context block, or the UNAVAILABLE
        marker.
    """
    return await recall_context(mm, intent, include_dag=False)
