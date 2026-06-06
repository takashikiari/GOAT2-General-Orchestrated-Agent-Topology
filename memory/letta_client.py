from __future__ import annotations

from memory.letta_blocks import do_get_block, do_set_block
from memory.letta_fallback import _InContextFallback
from memory.letta_health import LettaHealthProbe
from memory.letta_ops_list import do_clear, do_list
from memory.letta_ops_retrieve import do_retrieve, do_search
from memory.letta_ops_store import do_delete, do_store
from memory.letta_registry import LettaAgentRegistry
from memory.types import MemoryEntryMetadata, MemoryLayer

__all__ = ["LettaClient", "letta_client"]


class LettaClient(MemoryLayer):
    """Persistent memory via the Letta REST API with in-context fallback."""

    __slots__ = ("_probe", "_registry", "_fallback")

    def __init__(self) -> None:
        self._probe    = LettaHealthProbe()
        self._registry = LettaAgentRegistry(self._probe)
        self._fallback = _InContextFallback()

    async def store(self, agent_role, key, content, *, metadata=None, ttl=None):
        meta      = metadata or MemoryEntryMetadata(tags=[])
        user_tags = list(meta.get("tags") or [])
        if not await self._probe.is_available():
            return self._fallback.store(agent_role, key, content, meta)
        return await do_store(
            self._probe, self._registry, self._fallback,
            agent_role, key, content, meta, user_tags,
        )

    async def retrieve(self, agent_role, key):
        if not await self._probe.is_available():
            return self._fallback.retrieve(agent_role, key)
        return await do_retrieve(
            self._probe, self._registry, self._fallback, agent_role, key
        )

    async def search(self, agent_role, query, *, limit=5, tags=None):
        if not await self._probe.is_available():
            return self._fallback.search(agent_role, query, limit, tags)
        return await do_search(
            self._probe, self._registry, self._fallback, agent_role, query, limit, tags
        )

    async def delete(self, agent_role, key):
        if not await self._probe.is_available():
            return self._fallback.delete(agent_role, key)
        return await do_delete(
            self._probe, self._registry, self._fallback, agent_role, key
        )

    async def list(self, agent_role, *, limit=20):
        if not await self._probe.is_available():
            return self._fallback.list(agent_role, limit)
        return await do_list(
            self._probe, self._registry, self._fallback, agent_role, limit
        )

    async def clear(self, agent_role):
        if not await self._probe.is_available():
            return self._fallback.clear(agent_role)
        return await do_clear(self._probe, self._registry, self._fallback, agent_role)

    async def health(self) -> bool:
        return await self._probe.check(force=True)

    async def get_block(self, agent_role, label):
        if not await self._probe.is_available():
            return None
        return await do_get_block(self._probe, self._registry, agent_role, label)

    async def set_block(self, agent_role, label, value):
        if not await self._probe.is_available():
            return False
        return await do_set_block(self._probe, self._registry, agent_role, label, value)

    async def close(self) -> None:
        await self._probe.close()

    async def __aenter__(self) -> LettaClient: return self
    async def __aexit__(self, *_: object) -> None: await self.close()


letta_client = LettaClient()
