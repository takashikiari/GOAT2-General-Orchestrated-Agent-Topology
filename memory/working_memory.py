from __future__ import annotations

from memory.working_backend import StorageBackend
from memory.working_crud import WorkingCrudMixin
from memory.working_query import WorkingQueryMixin
from memory.working_sweep import WorkingSweepMixin

__all__ = ["WorkingMemoryLayer", "StorageBackend", "working_memory"]


class WorkingMemoryLayer(WorkingCrudMixin, WorkingQueryMixin, WorkingSweepMixin):
    """Session-scoped working memory with TTL, backed by a StorageBackend."""

    __slots__ = ("backend", "default_ttl", "_sweep_task")

    def __init__(
        self,
        backend:     StorageBackend | None = None,
        *,
        default_ttl: int = 3600,
    ) -> None:
        from memory.dict_backend import DictBackend
        self.backend:     StorageBackend  = backend or DictBackend()
        self.default_ttl: int             = default_ttl
        self._sweep_task                  = None


working_memory = WorkingMemoryLayer()
