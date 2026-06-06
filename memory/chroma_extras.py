from __future__ import annotations

import asyncio
import logging

from memory.chroma_types import _COLLECTION_PREFIX
from memory.chromadb_base import ChromaBase
from memory.types import AgentRole

log = logging.getLogger("goat2.memory.chroma")


class ChromaExtrasMixin(ChromaBase):
    """Introspection helpers for ChromaMemoryClient — not part of the MemoryLayer Protocol."""

    async def count(self, agent_role: AgentRole) -> int:
        """Return the total number of documents stored for agent_role."""
        try:
            return await asyncio.to_thread(
                lambda: self._get_collection(agent_role).count()
            )
        except Exception:
            return 0

    async def collections(self) -> list[str]:
        """Return names of all GOAT-owned ChromaDB collections (prefix goat2_)."""
        try:
            return await asyncio.to_thread(
                lambda: [
                    c.name for c in self._get_chroma().list_collections()
                    if c.name.startswith(_COLLECTION_PREFIX)
                ]
            )
        except Exception:
            return []
