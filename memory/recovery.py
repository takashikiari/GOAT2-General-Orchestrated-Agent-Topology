"""
memory.recovery — session-boundary memory operations.

    force_promote_all(working, episodic, chat_id): Promote ALL messages for
        chat_id to episodic, ignoring thresholds. Clears working memory.
    force_promote_all_chats(working, episodic): Call force_promote_all for all
        known chat_ids. Use before shutdown.
    recover_recent_context(working, episodic, chat_id, limit): On startup, if
        working memory is empty, restore the most recent entries from episodic.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from memory.config import RECOVERY_MESSAGE_LIMIT
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.episodic import EpisodicMemory
    from memory.working import WorkingMemory

log = get_logger(__name__)


async def force_promote_all(
    working: WorkingMemory, episodic: EpisodicMemory, chat_id: str
) -> int:
    """Promote ALL messages for chat_id to episodic, ignoring thresholds.

    Clears working memory for chat_id after promotion.
    Returns the number of messages promoted.
    """
    messages = await working.get_messages(chat_id)
    if not messages:
        return 0
    now = time.time()
    for msg in messages:
        await episodic.store(
            chat_id=chat_id,
            content=msg["content"],
            metadata={"role": msg["role"], "timestamp": float(msg.get("timestamp", now))},
        )
    await working.save_messages(chat_id, [])
    return len(messages)


async def force_promote_all_chats(
    working: WorkingMemory, episodic: EpisodicMemory
) -> int:
    """Force-promote all known chats at shutdown. Returns total promoted count."""
    total = 0
    for chat_id in await working.list_chat_ids():
        total += await force_promote_all(working, episodic, chat_id)
    log.info("Shutdown: force-promoted %d messages across all chats", total)
    return total


async def recover_recent_context(
    working: WorkingMemory,
    episodic: EpisodicMemory,
    chat_id: str,
    limit: int = RECOVERY_MESSAGE_LIMIT,
) -> None:
    """
    Restore recent context to working memory on startup.

    No-op if working memory already has content for chat_id — avoids
    duplicating context on a normal restart where working memory was not
    cleared.  Otherwise fetches the most recent ``limit`` entries from
    episodic.get_recent() and writes them into working memory.
    """
    if await working.get_messages(chat_id):
        return
    recent = await episodic.get_recent(chat_id, limit=limit)
    if not recent:
        return
    messages = [
        {
            "role": e["metadata"]["role"],
            "content": e["content"],
            "timestamp": float(e["metadata"].get("timestamp", 0)),
        }
        for e in recent
    ]
    await working.save_messages(chat_id, messages)
    log.info("Recovered %d messages for chat=%s", len(messages), chat_id)
