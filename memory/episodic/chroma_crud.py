"""ChromaCrudMixin — full CRUD + query + introspection for ChromaMemoryClient."""
from __future__ import annotations

import asyncio
import logging
from typing import Final

from memory.episodic.chroma_helpers import (
    _build_chroma_metadata, _collection_name, _doc_id, _has_all_tags,
    _now_iso, _now_ts, _str_to_tags,
)
from memory.episodic.chroma_parsers import _parse_get_result, _parse_query_result
from memory.episodic.chroma_types import (
    ChromaGetResult, ChromaQueryResult,
    _COLLECTION_PREFIX, _LIST_FETCH_MAX, _SEARCH_TAG_OVERSAMPLE,
)
from memory.episodic.chromadb_base import ChromaBase
from memory.shared.last_write import sync_last_write
from memory.shared.types import AgentRole, IsoTimestamp, MemoryEntry, MemoryEntryMetadata, MemoryKey

log = logging.getLogger("goat2.memory.chroma")
_SOURCE: Final[str] = "chroma"


class ChromaCrudMixin(ChromaBase):
    """Store, retrieve, delete, search, list, health for ChromaMemoryClient."""

    async def store(
        self, agent_role: AgentRole, key: MemoryKey, content: str,
        *, metadata: MemoryEntryMetadata | None = None, ttl: int | None = None,
    ) -> MemoryEntry:
        """Persist content to ChromaDB; updates Redis last-write timestamp."""
        ts = _now_ts()
        iso = _now_iso()
        meta = _build_chroma_metadata(agent_role, key, metadata, ts, iso)
        doc_id = _doc_id(agent_role, key)

        def _sync() -> None:
            self._get_collection(agent_role).upsert(
                ids=[doc_id], documents=[content], metadatas=[meta]
            )

        try:
            await asyncio.to_thread(_sync)
            log.debug("store(%s, %s) → %s", agent_role, key, doc_id)
        except Exception as exc:
            log.error("store(%s, %s) failed: %s", agent_role, key, exc)
            raise

        await self._sync_last_write_to_redis()

        return MemoryEntry(
            id=doc_id, agent_role=agent_role, key=key, content=content,
            metadata=MemoryEntryMetadata(
                tags=_str_to_tags(meta["tags"]), created_at_ts=float(ts),
            ),
            created_at=iso, source=_SOURCE,
        )

    async def _sync_last_write_to_redis(self) -> None:
        """Update Redis last-write timestamp for chromadb tier (fail-silent).

        Delegates to ``memory.shared.last_write.sync_last_write`` which
        resolves the registry-owned working backend — no fresh backend
        instance is created here.
        """
        try:
            await sync_last_write("episodic", iso_format=True)
        except Exception as exc:
            log.debug("Chroma last-write sync failed (non-blocking): %s", exc)

    async def retrieve(self, agent_role: AgentRole, key: MemoryKey) -> MemoryEntry | None:
        """Retrieve by exact key; None if not found."""
        doc_id = _doc_id(agent_role, key)

        def _sync() -> ChromaGetResult:
            return self._get_collection(agent_role).get(  # type: ignore[return-value]
                ids=[doc_id], include=["documents", "metadatas"]
            )

        try:
            result = await asyncio.to_thread(_sync)
        except Exception as exc:
            log.error("retrieve(%s, %s) failed: %s", agent_role, key, exc)
            return None

        entries = _parse_get_result(result, agent_role)
        return entries[0] if entries else None

    async def get(self, agent_role: AgentRole, key: MemoryKey) -> MemoryEntry | None:
        """Protocol read path: retrieve by key and bump access stats."""
        entry = await self.retrieve(agent_role, key)
        if entry is not None:
            await self._bump_access(agent_role, key)
        return entry

    async def _bump_access(self, agent_role: AgentRole, key: MemoryKey) -> None:
        """Increment access_count and refresh accessed_at_ts for one entry."""
        doc_id = _doc_id(agent_role, key)

        def _sync() -> None:
            col = self._get_collection(agent_role)
            res = col.get(ids=[doc_id], include=["documents", "metadatas"])
            if not res.get("ids"):
                return
            meta = dict((res.get("metadatas") or [None])[0] or {})
            doc = (res.get("documents") or [None])[0] or ""
            meta["access_count"] = int(meta.get("access_count", 0) or 0) + 1
            meta["accessed_at_ts"] = float(_now_ts())
            col.upsert(ids=[doc_id], documents=[doc], metadatas=[meta])

        try:
            await asyncio.to_thread(_sync)
            log.debug("get: access bumped %s/%s", agent_role, key)
        except Exception as exc:
            log.debug("get: access bump skipped: %s", exc)

    async def delete(self, agent_role: AgentRole, key: MemoryKey) -> bool:
        """Delete by key; True if existed."""
        doc_id = _doc_id(agent_role, key)

        def _check() -> bool:
            res: ChromaGetResult = self._get_collection(agent_role).get(  # type: ignore[assignment]
                ids=[doc_id], include=[]
            )
            return bool(res.get("ids"))

        def _delete() -> None:
            self._get_collection(agent_role).delete(ids=[doc_id])

        try:
            existed = await asyncio.to_thread(_check)
            if existed:
                await asyncio.to_thread(_delete)
                log.debug("delete(%s, %s) → removed", agent_role, key)
            return existed
        except Exception as exc:
            log.error("delete(%s, %s) failed: %s", agent_role, key, exc)
            return False

    async def search(
        self, agent_role: AgentRole, query: str,
        *, limit: int = 5, tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Semantic search over agent_role's collection; optional tag filter."""
        def _sync() -> ChromaQueryResult:
            col = self._get_collection(agent_role)
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

    async def list(self, agent_role: AgentRole, limit: int = 20) -> list[MemoryEntry]:
        """Return up to limit most-recent entries for agent_role, sorted by timestamp."""
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
        """Drop the entire collection for agent_role; returns count of removed docs."""
        def _sync() -> int:
            col = self._get_collection(agent_role)
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
        """Return True if ChromaDB is reachable."""
        def _sync() -> bool:
            try:
                self._get_chroma().list_collections()
                return True
            except Exception as exc:
                log.warning("ChromaDB health check failed: %s", exc)
                return False

        return await asyncio.to_thread(_sync)

    async def count(self, agent_role: AgentRole) -> int:
        """Return total number of documents stored for agent_role."""
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

    async def get_embedding(
        self, agent_role: AgentRole, key: MemoryKey,
    ) -> list[float] | None:
        """Retrieve the embedding vector for a stored document; None if missing."""
        doc_id = _doc_id(agent_role, key)

        def _sync() -> list[float] | None:
            result = self._get_collection(agent_role).get(
                ids=[doc_id], include=["embeddings"]
            )
            embeddings = result.get("embeddings") or []
            return list(embeddings[0]) if embeddings else None

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            log.warning("get_embedding(%s, %s) failed: %s", agent_role, key, exc)
            return None
