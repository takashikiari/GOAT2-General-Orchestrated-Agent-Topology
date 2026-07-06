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
"""

from __future__ import annotations

import asyncio

from memory.config import WORKING_MAX_MESSAGES
from memory.working.working import WorkingMemory
from utils.logging.setup import get_logger

log = get_logger(__name__)

# Maximum messages to drop in a single chunk. For surpluses larger than this
# (e.g. a first-run with a huge backlog) the while loop runs multiple
# iterations, yielding between them so other coroutines are not starved.
PROMOTE_CHUNK_SIZE = 50


async def maybe_auto_promote(
    chat_id: str,
    working: WorkingMemory,
    episodic=None,
    extractor=None,
) -> None:
    """Trim L2 working memory to WORKING_MAX_MESSAGES and enrich dropped entries.

    For each dropped user+assistant pair that has an ``l3_id`` field, fires
    ``pair_and_enrich_dropped`` to update the corresponding L3 ChromaDB entry
    with GLiNER-extracted entities, memory_type, and importance.
    """
    from memory.enrichment import pair_and_enrich_dropped  # local import avoids circular
    async with working.chat_lock(chat_id):
        messages = await working.get_messages_raw(chat_id)
        total = len(messages)
        if total <= WORKING_MAX_MESSAGES:
            return
        surplus = total - WORKING_MAX_MESSAGES
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
        asyncio.create_task(pair_and_enrich_dropped(all_dropped, episodic, extractor))


def schedule_auto_promote(
    chat_id: str,
    working: WorkingMemory,
    episodic=None,
    extractor=None,
) -> None:
    """Fire-and-forget: schedule a background L2 trim + L3 enrichment for chat_id."""
    asyncio.create_task(maybe_auto_promote(chat_id, working, episodic=episodic, extractor=extractor))
