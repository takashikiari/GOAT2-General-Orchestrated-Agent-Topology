"""WorkingMemoryLayer — Session-scoped working memory with TTL.

Backed by a StorageBackend (DictBackend or RedisBackend). Provides
fast, ephemeral storage for active conversation context.
"""
from __future__ import annotations

from memory.working_backend import StorageBackend
from memory.working_crud import WorkingCrudMixin
from memory.working_query import WorkingQueryMixin
from memory.working_sweep import WorkingSweepMixin

__all__ = ["WorkingMemoryLayer", "StorageBackend", "working_memory"]


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
        from memory.dict_backend import DictBackend

        self.backend: StorageBackend = backend or DictBackend()
        self.default_ttl: int = default_ttl
        self._sweep_task = None


from memory.redis_backend import RedisBackend
working_memory = WorkingMemoryLayer(backend=RedisBackend())
