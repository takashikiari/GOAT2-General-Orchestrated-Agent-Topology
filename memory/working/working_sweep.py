"""Background TTL eviction for working memory.

No-op on RedisBackend (server handles TTL natively via EXPIRE). For
DictBackend, exposes a sweep() method and a start_sweep_task() coroutine that
fires it on an interval.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.working.working_sweep")

__all__ = ["WorkingSweepMixin"]


class WorkingSweepMixin:
    """TTL eviction helpers for ``WorkingMemoryLayer``."""

    backend:     "WorkingMemoryBackend"
    _sweep_task: asyncio.Task | None

    async def sweep(self) -> int:
        """Evict expired ``DictBackend`` entries now; no-op for other backends.

        Returns:
            Number of expired entries removed (0 when the backend is not a
            ``DictBackend``).
        """
        from memory.working.dict_backend import DictBackend
        if isinstance(self.backend, DictBackend):
            return self.backend.sweep()
        return 0

    async def start_sweep_task(self, interval: float = 60.0) -> asyncio.Task:
        """
        Launch a background asyncio task that calls sweep() every `interval` seconds.
        Idempotent — returns the existing task if already running.
        """
        if self._sweep_task is not None and not self._sweep_task.done():
            return self._sweep_task

        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                removed = await self.sweep()
                if removed:
                    log.debug("sweep task: evicted %d expired entries", removed)

        self._sweep_task = asyncio.get_event_loop().create_task(
            _loop(), name="working_memory_sweep"
        )
        log.info("Started working-memory sweep task (interval=%.0fs)", interval)
        return self._sweep_task
