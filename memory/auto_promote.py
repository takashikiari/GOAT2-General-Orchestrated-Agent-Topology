"""memory.auto_promote — L2 working-memory size manager (async background).

Trims working memory to ``WORKING_MAX_MESSAGES`` by dropping the oldest messages
once L2 exceeds the cap. Runs fire-and-forget after every turn save.

L3 archival is NOT performed here. ``_archive_turn`` in the orchestrator writes
every turn to L3 immediately as a ``user+assistant`` pair tagged
``l2_full_archive``. Writing to L3 here as well would create duplicate entries
in ChromaDB — same content under two separate IDs — polluting search results and
blended scores for all future turns. The two paths must stay separate:

    _archive_turn      → L3 write (every turn, immediate, user+assistant pair)
    maybe_auto_promote → L2 trim only (when cap exceeded, oldest-first)
                         + L3 enrichment of existing entries (enrich_l3_entry)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from memory.config import WORKING_MAX_MESSAGES
from memory.config_extra import AUTO_PROMOTE_CHUNK_SIZE, AUTO_PROMOTE_MIN_SURPLUS
from memory.working.working import WorkingMemory
from utils.logging.setup import get_logger

log = get_logger(__name__)

PROMOTE_CHUNK_SIZE = AUTO_PROMOTE_CHUNK_SIZE
# Minimum surplus before trimming fires. Prevents every-turn ping-pong when
# a conversation is exactly at cap: 2 messages added → 2 dropped → repeat.
PROMOTE_MIN_SURPLUS = AUTO_PROMOTE_MIN_SURPLUS


async def maybe_auto_promote(
    chat_id: str,
    working: WorkingMemory,
    episodic=None,
    extractor=None,
    cache_clear_fn: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Trim L2 to WORKING_MAX_MESSAGES; enrich dropped entries; clear search cache.

    ``cache_clear_fn``: async callable (no args) to invalidate the
    session cache for this chat after enrichment completes. Passed by
    MemoryLayers so stale cached search results are not served after the
    L3 metadata update. Optional — callers that do not own a cache omit it.
    """
    from memory.enrichment import pair_and_enrich_dropped
    async with working.chat_lock(chat_id):
        messages = await working.get_messages_raw(chat_id)
        total = len(messages)
        surplus = total - WORKING_MAX_MESSAGES
        if surplus < PROMOTE_MIN_SURPLUS:
            return
        log.info(
            "auto_promote: chat=%s total=%d surplus=%d cap=%d",
            chat_id, total, surplus, WORKING_MAX_MESSAGES,
        )
        dropped_total = 0
        all_dropped: list[dict] = []
        while len(messages) > WORKING_MAX_MESSAGES:
            surplus_now = len(messages) - WORKING_MAX_MESSAGES
            chunk_size = min(PROMOTE_CHUNK_SIZE, surplus_now)
            dropped = messages[:chunk_size]
            messages = messages[chunk_size:]
            all_dropped.extend(dropped)
            dropped_total += len(dropped)
            await working.save_messages_raw(chat_id, messages)
            log.debug(
                "auto_promote: chat=%s chunk_dropped=%d total_dropped=%d remaining=%d",
                chat_id, len(dropped), dropped_total, len(messages),
            )
            await asyncio.sleep(0)

    log.info(
        "auto_promote: chat=%s done total_dropped=%d kept=%d",
        chat_id, dropped_total, WORKING_MAX_MESSAGES,
    )
    if all_dropped and episodic is not None:
        await pair_and_enrich_dropped(all_dropped, episodic, extractor)
        if cache_clear_fn is not None:
            try:
                await cache_clear_fn()
            except Exception as exc:  # noqa: BLE001
                log.warning("auto_promote: cache_clear_fn failed chat=%s: %s", chat_id, exc)


def schedule_auto_promote(
    chat_id: str,
    working: WorkingMemory,
    episodic=None,
    extractor=None,
    cache_clear_fn: Callable[[], Awaitable[None]] | None = None,
) -> asyncio.Task:
    """Fire-and-forget: schedule L2 trim + L3 enrichment + cache invalidation.

    Returns the asyncio.Task so callers can track it for clean shutdown.
    """
    return asyncio.create_task(
        maybe_auto_promote(
            chat_id, working, episodic=episodic, extractor=extractor,
            cache_clear_fn=cache_clear_fn,
        )
    )
