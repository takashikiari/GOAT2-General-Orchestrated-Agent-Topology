"""WorkingMemoryLayer — Session-scoped working memory with TTL.

Backed by a ``WorkingMemoryBackend`` (``DictBackend`` or ``RedisBackend``).
Provides fast, ephemeral storage for active conversation context.

PHASE 4 UPDATE:
===============
Module-level `working_memory = WorkingMemoryLayer(...)` singleton REMOVED.
All code must now use Registry for working memory access.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.working.working_crud import WorkingCrudMixin
from memory.working.working_query import WorkingQueryMixin
from memory.working.working_sweep import WorkingSweepMixin

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.working.working_memory")

__all__ = ["WorkingMemoryLayer"]


class WorkingMemoryLayer(
    WorkingCrudMixin, WorkingQueryMixin, WorkingSweepMixin
):
    """
    Session-scoped working memory with TTL.

    Backed by a ``WorkingMemoryBackend`` (``DictBackend`` or ``RedisBackend``).
    Provides fast, ephemeral storage for active conversation context.

    TTL is enforced lazily on read for DictBackend, server-side for Redis.
    Call sweep() or start_sweep_task() for proactive eviction.
    """

    __slots__ = ("backend", "default_ttl", "_sweep_task")

    def __init__(
        self,
        backend: "WorkingMemoryBackend | None" = None,
        *,
        default_ttl: int = 3600,
    ) -> None:
        self.backend: "WorkingMemoryBackend" = backend or self._default_backend()
        self.default_ttl: int = default_ttl
        self._sweep_task = None
        log.debug(
            "WorkingMemoryLayer: initialised (backend=%s default_ttl=%d)",
            type(self.backend).__name__, self.default_ttl,
        )

    @staticmethod
    def _default_backend() -> "WorkingMemoryBackend":
        """Lazy default backend — avoids hard import at module load."""
        from memory.working.redis_backend import RedisBackend
        return RedisBackend()
