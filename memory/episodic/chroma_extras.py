from __future__ import annotations

import asyncio
import logging

from memory.episodic.chroma_types import _COLLECTION_PREFIX
from memory.episodic.chromadb_base import ChromaBase
from memory.shared.types import AgentRole

log = logging.getLogger("goat2.memory.chroma")


class ChromaExtrasMixin(ChromaBase):
    """Introspection helpers for ChromaMemoryClient — not part of the MemoryLayer Protocol."""

    async def count(self, agent_role: AgentRole) -> int:
        """Return the total number of documents stored for agent_role."""
        try:
            count = await asyncio.to_thread(
                lambda: self._get_collection(agent_role).count()
            )
            log.debug("chroma.count: role=%r → %d", agent_role, count)
            return count
        except Exception as exc:
            log.warning("chroma.count: error for role=%r: %s", agent_role, exc)
            return 0

    async def collections(self) -> list[str]:
        """Return names of all GOAT-owned ChromaDB collections (prefix goat2_)."""
        try:
            names = await asyncio.to_thread(
                lambda: [
                    c.name for c in self._get_chroma().list_collections()
                    if c.name.startswith(_COLLECTION_PREFIX)
                ]
            )
            log.debug("chroma.collections: %d collections", len(names))
            return names
        except Exception as exc:
            log.warning("chroma.collections: error: %s", exc)
            return []
