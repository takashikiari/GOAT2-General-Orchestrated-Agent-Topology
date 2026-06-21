"""
memory.promotion — background daemon that promotes stale WorkingMemory to EpisodicMemory.

Runs a periodic loop (PROMOTION_CHECK_INTERVAL_SECONDS).  For each chat_id,
delegates to _check_and_promote() which applies age and count thresholds.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from memory.config import PROMOTION_CHECK_INTERVAL_SECONDS, PROMOTION_MAX_AGE_SECONDS, PROMOTION_MAX_MESSAGES
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.episodic import EpisodicMemory
    from memory.working import WorkingMemory

log = get_logger(__name__)


class PromotionDaemon:
    """
    Periodically promotes stale WorkingMemory entries to EpisodicMemory.
    Not a singleton — owned by the caller (e.g. the Telegram bot).
    """

    def __init__(self, working_memory: WorkingMemory, episodic_memory: EpisodicMemory) -> None:
        self._working = working_memory
        self._episodic = episodic_memory
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Launch the promotion loop as a background asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("PromotionDaemon started (interval=%ds)", PROMOTION_CHECK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Cancel the loop and wait for it to finish cleanly."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("PromotionDaemon stopped")

    async def _loop(self) -> None:
        """Check every known chat_id each interval; sleep between cycles."""
        while self._running:
            try:
                for chat_id in await self._working.list_chat_ids():
                    n = await self._check_and_promote(chat_id)
                    if n:
                        log.info("promoted %d msg(s) from chat=%s", n, chat_id)
            except Exception:
                log.exception("PromotionDaemon: check cycle failed")
            try:
                await asyncio.sleep(PROMOTION_CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _check_and_promote(self, chat_id: str) -> int:
        """Promote stale msgs for chat_id (age then count threshold). Returns promoted count."""
        messages = await self._working.get_messages(chat_id)
        if not messages:
            return 0
        now = time.time()
        to_keep, to_promote = [], []
        for msg in messages:
            age = now - float(msg.get("timestamp", now))
            (to_promote if age > PROMOTION_MAX_AGE_SECONDS else to_keep).append(msg)
        if len(to_keep) > PROMOTION_MAX_MESSAGES:
            excess = len(to_keep) - PROMOTION_MAX_MESSAGES
            to_promote += to_keep[:excess]
            to_keep = to_keep[excess:]
        for msg in to_promote:
            await self._episodic.store(
                chat_id=chat_id,
                content=msg["content"],
                metadata={"role": msg["role"], "timestamp": float(msg.get("timestamp", now))},
            )
        if to_promote:
            await self._working.save_messages(chat_id, to_keep)
        return len(to_promote)
