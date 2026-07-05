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


async def maybe_auto_promote(chat_id: str, working: WorkingMemory) -> None:
    """Trim L2 working memory to ``WORKING_MAX_MESSAGES``, dropping oldest first.

    Holds the per-chat lock for the entire read → trim → save cycle so no
    concurrent turn save can overwrite the reduced message list mid-trim. Uses
    the raw (no-lock) variants internally because the lock is already held.

    When the surplus exceeds ``PROMOTE_CHUNK_SIZE``, multiple iterations run
    with an ``asyncio.sleep(0)`` yield between them so the event loop stays
    responsive under a large backlog. Each iteration saves the reduced list
    immediately after dropping a chunk, so a crash mid-backlog does not lose
    more than one chunk.

    Does NOT write to L3 — ``_archive_turn`` (orchestrator) handles that on
    every turn. Writing here too would produce duplicate entries in ChromaDB.
    """
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

        while len(messages) > WORKING_MAX_MESSAGES:
            # Drop at most PROMOTE_CHUNK_SIZE, but never more than the actual
            # surplus — this is the fix for the previous bug where chunk_size
            # (50) > cap (20) caused the entire L2 to be emptied in one pass.
            surplus_now = len(messages) - WORKING_MAX_MESSAGES
            chunk_size = min(PROMOTE_CHUNK_SIZE, surplus_now)
            dropped = messages[:chunk_size]
            messages = messages[chunk_size:]
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


def schedule_auto_promote(chat_id: str, working: WorkingMemory) -> None:
    """Fire-and-forget: schedule a background L2 trim for ``chat_id``."""
    asyncio.create_task(maybe_auto_promote(chat_id, working))
