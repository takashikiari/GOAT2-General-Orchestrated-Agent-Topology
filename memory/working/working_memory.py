"""WorkingMemoryLayer — Session-scoped working memory with TTL.

Backed by a StorageBackend (DictBackend or RedisBackend). Provides
fast, ephemeral storage for active conversation context.

PHASE 4 UPDATE:
===============
Module-level `working_memory = WorkingMemoryLayer(...)` singleton REMOVED.
All code must now use Registry for working memory access.
"""
from __future__ import annotations

from memory.working.working_backend import StorageBackend
from memory.working.working_crud import WorkingCrudMixin
from memory.working.working_query import WorkingQueryMixin
from memory.working.working_sweep import WorkingSweepMixin

__all__ = ["WorkingMemoryLayer", "StorageBackend"]


class WorkingMemoryLayer(
    WorkingCrudMixin, WorkingQueryMixin, WorkingSweepMixin
):
    """
    Session-scoped working memory with TTL.

    Backed by a StorageBackend (DictBackend or RedisBackend).
    Provides fast, ephemeral storage for active conversation context.

    TTL is enforced lazily on read for DictBackend, server-side for Redis.
    Call sweep() or start_sweep_task() for proactive eviction.
    """

    __slots__ = ("backend", "default_ttl", "_sweep_task")

    def __init__(
        self,
        backend: StorageBackend | None = None,
        *,
        default_ttl: int = 3600,
    ) -> None:
        from memory.working.dict_backend import DictBackend

        self.backend: StorageBackend = backend or DictBackend()
        self.default_ttl: int = default_ttl
        self._sweep_task = None
