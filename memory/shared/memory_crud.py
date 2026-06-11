"""MemoryCrudMixin — Core CRUD routing for MemoryManager.

Delegates to the correct layer based on memory_type. Provides
store, retrieve, locate, delete, list, and clear operations.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.shared.memory_enums import MemoryType

if TYPE_CHECKING:
    from memory.shared.types import MemoryEntry, MemoryEntryMetadata, MemoryLayer
    from memory.working.working_memory import WorkingMemoryLayer

log = logging.getLogger("goat2.memory.shared")


class MemoryCrudMixin:
    """
    Core CRUD routing for MemoryManager.

    Delegates to the correct layer via _layer(). All operations
    are async for consistency with I/O backends.
    """

    _layers: dict[MemoryType, MemoryLayer]

    def _layer(self, memory_type: MemoryType | str) -> MemoryLayer:
        """Get layer instance from MemoryType or string name."""
        return self._layers[MemoryType(memory_type)]

    async def store(
        self,
        agent_role: str,
        key: str,
        content: str,
        *,
        memory_type: MemoryType | str = MemoryType.WORKING,
        metadata: MemoryEntryMetadata | None = None,
        ttl: int | None = None,
    ) -> MemoryEntry:
        """
        Persist content in the given tier.

        ttl is forwarded only to WORKING layer; EPISODIC and LONG_TERM
        use their own persistence semantics and ignore ttl.
        """
        from memory.working.working_memory import WorkingMemoryLayer

        layer = self._layer(memory_type)
        if isinstance(layer, WorkingMemoryLayer):
            return await layer.store(
                agent_role, key, content, metadata=metadata, ttl=ttl
            )
        entry = await layer.store(agent_role, key, content, metadata=metadata)
        log.debug("store(%s, %s) → %s", agent_role, key, memory_type)
        try:
            import time
            from memory.working.redis_backend import RedisBackend
            redis = RedisBackend()
            r = await redis._get_redis()
            tier = str(memory_type.value if hasattr(memory_type, "value") else memory_type)
            await r.set(f"goat2:working:last_write:{tier}", str(time.time()))
            await redis.close()
        except Exception:
            pass
        return entry

    async def retrieve(
        self,
        agent_role: str,
        key: str,
        *,
        memory_type: MemoryType | str = MemoryType.WORKING,
    ) -> MemoryEntry | None:
        """Retrieve entry by exact key from specified tier."""
        result = await self._layer(memory_type).retrieve(agent_role, key)
        log.debug("retrieve(%s, %s, %s) → %s", agent_role, key, memory_type, "hit" if result else "miss")
        return result

    async def locate(
        self,
        agent_role: str,
        key: str,
        *,
        memory_type: MemoryType | str | None = None,
    ) -> MemoryEntry | None:
        """
        Find entry by exact key across tiers.

        If memory_type is None, probes all three tiers in priority order
        (WORKING → EPISODIC → LONG_TERM) and returns first hit.
        """
        if memory_type is not None:
            return await self.retrieve(agent_role, key, memory_type=memory_type)
        for mt in MemoryType.priority_order():
            entry = await self.retrieve(agent_role, key, memory_type=mt)
            if entry is not None:
                return entry
        return None

    async def delete(
        self,
        agent_role: str,
        key: str,
        *,
        memory_type: MemoryType | str,
    ) -> bool:
        """Delete entry from specified tier; True if existed."""
        return await self._layer(memory_type).delete(agent_role, key)

    async def list(
        self,
        agent_role: str,
        *,
        memory_type: MemoryType | str,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """List entries from specified tier; limited."""
        return await self._layer(memory_type).list(agent_role, limit=limit)

    async def clear(
        self, agent_role: str, *, memory_type: MemoryType | str
    ) -> int:
        """Clear all entries from specified tier; returns count."""
        count = await self._layer(memory_type).clear(agent_role)
        log.info("clear(%s, %s): removed %d", agent_role, memory_type, count)
        return count
