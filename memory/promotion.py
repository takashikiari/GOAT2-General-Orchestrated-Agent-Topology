"""memory.promotion — daemon: WorkingMemory→EpisodicMemory→PermanentMemory promotion."""
from __future__ import annotations

import asyncio, time  # noqa: E401
from typing import TYPE_CHECKING

from memory.config import (
    EPISODIC_MAX_ENTRIES, EPISODIC_PROMOTE_COUNT, PROMOTION_CHECK_INTERVAL_SECONDS,
    PROMOTION_MAX_AGE_SECONDS, PROMOTION_MAX_MESSAGES,
)
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.episodic import EpisodicMemory
    from memory.permanent import PermanentMemory
    from memory.working import WorkingMemory

log = get_logger(__name__)


class PromotionDaemon:
    """Promotes stale entries up the memory tier chain on a periodic schedule."""

    def __init__(
        self, working_memory: WorkingMemory,
        episodic_memory: EpisodicMemory, permanent_memory: PermanentMemory,
    ) -> None:
        self._working = working_memory
        self._episodic = episodic_memory
        self._permanent = permanent_memory
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running, self._task = True, asyncio.create_task(self._loop())
        log.info("PromotionDaemon started (interval=%ds)", PROMOTION_CHECK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
        log.info("PromotionDaemon stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                for chat_id in await self._working.list_chat_ids():
                    n = await self._check_and_promote(chat_id)
                    if n:
                        log.info("promoted %d msg(s) from chat=%s", n, chat_id)
                await self._check_episodic_promotion()
            except Exception:
                log.exception("PromotionDaemon: check cycle failed")
            try:
                await asyncio.sleep(PROMOTION_CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _check_and_promote(self, chat_id: str) -> int:
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
            ts = float(msg.get("timestamp", now))
            await self._episodic.store(chat_id, msg["content"],
                                        {"role": msg["role"], "timestamp": ts})
        if to_promote:
            await self._working.save_messages(chat_id, to_keep)
        return len(to_promote)

    async def _check_episodic_promotion(self) -> int:
        """Global episodic→permanent: promote oldest EPISODIC_PROMOTE_COUNT when total >= EPISODIC_MAX_ENTRIES."""
        if (total := await self._episodic.count()) < EPISODIC_MAX_ENTRIES:
            return 0
        oldest = await self._episodic.get_oldest(EPISODIC_PROMOTE_COUNT)
        await self._permanent.archive_entries(oldest)
        await self._episodic.delete_entries([e["id"] for e in oldest])
        log.info("PromotionDaemon: episodic→permanent %d entries (was %d)", len(oldest), total)
        return len(oldest)
