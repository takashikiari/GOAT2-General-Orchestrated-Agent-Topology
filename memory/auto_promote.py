"""memory.auto_promote — automatic L2 → L3 promotion hook (async background)."""

from __future__ import annotations

import asyncio
from memory.config import WORKING_MAX_MESSAGES
from memory.episodic.episodic import EpisodicMemory
from memory.working.working import WorkingMemory
from utils.logging.setup import get_logger

log = get_logger(__name__)

# Câte mesaje se promovează per lot
PROMOTE_CHUNK_SIZE = 50


async def maybe_auto_promote(chat_id: str) -> None:
    """Check working memory size and promote surplus entries to L3.
    
    Runs in background. If there are more than WORKING_MAX_MESSAGES,
    promotes surplus in chunks of PROMOTE_CHUNK_SIZE, saving progress
    to working after each chunk.
    """
    working = WorkingMemory()
    messages = await working.get_messages(chat_id)
    total = len(messages)
    
    if total <= WORKING_MAX_MESSAGES:
        return

    # Câte mesaje trebuie promovate
    to_promote_count = total - WORKING_MAX_MESSAGES
    log.info(f"auto_promote: chat={chat_id} total={total} to_promote={to_promote_count}")

    episodic = EpisodicMemory()
    promoted_total = 0

    # Promovează în chunk-uri, începând cu cele mai vechi
    while len(messages) > WORKING_MAX_MESSAGES:
        # Ia un chunk de mesaje vechi
        promote = messages[:PROMOTE_CHUNK_SIZE]
        messages = messages[PROMOTE_CHUNK_SIZE:]

        # Scrie în episodică
        for msg in promote:
            content = msg.get("content", "")
            if not content:
                continue
            metadata = {
                "timestamp": msg.get("timestamp", 0.0),
                "role": msg.get("role", "unknown"),
                "source": "auto_promote",
            }
            await episodic.store(chat_id=chat_id, content=content, metadata=metadata)
            promoted_total += 1

        # Salvează progresul în working (păstrează doar mesajele rămase)
        await working.save_messages(chat_id, messages)

        log.debug(f"auto_promote: chat={chat_id} promoted={promoted_total} remaining={len(messages)}")

        # Cedează controlul altor task-uri
        await asyncio.sleep(0)

    log.info(f"auto_promote: chat={chat_id} done promoted={promoted_total} kept={WORKING_MAX_MESSAGES}")


def schedule_auto_promote(chat_id: str) -> None:
    """Fire-and-forget auto-promote task (runs in background)."""
    asyncio.create_task(maybe_auto_promote(chat_id))
