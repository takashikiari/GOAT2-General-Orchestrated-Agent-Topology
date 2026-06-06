from __future__ import annotations

import asyncio
import logging

from memory.chroma_helpers import _collection_name, _has_all_tags
from memory.chroma_parsers import _parse_get_result, _parse_query_result
from memory.chroma_types import (
    ChromaGetResult, ChromaQueryResult, _LIST_FETCH_MAX, _SEARCH_TAG_OVERSAMPLE,
)
from memory.chromadb_base import ChromaBase
from memory.types import AgentRole, MemoryEntry

log = logging.getLogger("goat2.memory.chroma")


class ChromaQueryMixin(ChromaBase):
    """search, list, clear, health for ChromaMemoryClient."""

    async def search(
        self, agent_role: AgentRole, query: str,
        *, limit: int = 5, tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        def _sync() -> ChromaQueryResult:
            col   = self._get_collection(agent_role)
            count = col.count()
            if count == 0:
                return ChromaQueryResult(ids=[[]], documents=[[]], metadatas=[[]])  # type: ignore[typeddict-item]
            n = min(limit * _SEARCH_TAG_OVERSAMPLE if tags else limit, count)
            return col.query(  # type: ignore[return-value]
                query_texts=[query], n_results=max(1, n),
                where={"agent_role": str(agent_role)},
                include=["documents", "metadatas"],
            )

        try:
            result = await asyncio.to_thread(_sync)
        except Exception as exc:
            log.error("search(%s, %r) failed: %s", agent_role, query[:60], exc)
            return []

        entries = _parse_query_result(result, agent_role)
        if tags:
            entries = [e for e in entries if _has_all_tags(e, tags)]
        return entries[:limit]

    async def list(self, agent_role: AgentRole, *, limit: int = 20) -> list[MemoryEntry]:
        def _sync() -> ChromaGetResult:
            return self._get_collection(agent_role).get(  # type: ignore[return-value]
                limit=_LIST_FETCH_MAX, include=["documents", "metadatas"]
            )

        try:
            result = await asyncio.to_thread(_sync)
        except Exception as exc:
            log.error("list(%s) failed: %s", agent_role, exc)
            return []

        entries = _parse_get_result(result, agent_role)
        entries.sort(
            key=lambda e: float(e.metadata.get("created_at_ts") or 0), reverse=True
        )
        return entries[:limit]

    async def clear(self, agent_role: AgentRole) -> int:
        def _sync() -> int:
            col   = self._get_collection(agent_role)
            count = col.count()
            self._get_chroma().delete_collection(_collection_name(agent_role))
            self._cols.pop(agent_role, None)
            log.info("clear(%s): dropped collection (%d docs)", agent_role, count)
            return count

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            log.error("clear(%s) failed: %s", agent_role, exc)
            return 0

    async def health(self) -> bool:
        def _sync() -> bool:
            try:
                self._get_chroma().list_collections()
                return True
            except Exception as exc:
                log.warning("ChromaDB health check failed: %s", exc)
                return False

        return await asyncio.to_thread(_sync)
