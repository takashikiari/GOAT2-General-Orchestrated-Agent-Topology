"""ChromaCrudMixin — store, retrieve, delete for ChromaMemoryClient.

Wraps ChromaDB CRUD operations with Redis sync for last-write tracking.
Whenever an entry is stored, updates Redis key goat2:working:last_write:chromadb
with the current ISO 8601 timestamp.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Final

from memory.episodic.chroma_helpers import (
    _build_chroma_metadata, _doc_id, _now_iso, _now_ts, _str_to_tags,
)
from memory.episodic.chroma_parsers import _parse_get_result
from memory.episodic.chroma_types import ChromaGetResult
from memory.episodic.chromadb_base import ChromaBase
from memory.shared.types import AgentRole, IsoTimestamp, MemoryEntry, MemoryEntryMetadata, MemoryKey

log = logging.getLogger("goat2.memory.chroma")
_SOURCE: Final[str] = "chroma"


class ChromaCrudMixin(ChromaBase):
    """Store, retrieve, delete for ChromaMemoryClient with Redis last-write sync."""

    async def store(
        self, agent_role: AgentRole, key: MemoryKey, content: str,
        *, metadata: MemoryEntryMetadata | None = None, ttl: int | None = None,
    ) -> MemoryEntry:
        """Persist content to ChromaDB; also updates Redis last-write timestamp.

        TTL is ignored for ChromaDB (persistent storage). Metadata tags are
        stored as comma-separated string for filtering.

        Redis sync: updates goat2:working:last_write:chromadb with ISO timestamp.
        Fails silently if Redis is unavailable (does not block main write).
        """
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

        # Sync last-write timestamp to Redis (non-blocking, fail-silent)
        await self._sync_last_write_to_redis()

        return MemoryEntry(
            id=doc_id, agent_role=agent_role, key=key, content=content,
            metadata=MemoryEntryMetadata(
                tags=_str_to_tags(meta["tags"]), created_at_ts=float(ts),
            ),
            created_at=iso, source=_SOURCE,
        )

    
    async def list(self, agent_role: str, limit: int = 50) -> list:
        """Return up to `limit` most recent entries for agent_role from ChromaDB."""
        try:
            collection = self._get_collection(agent_role)
            # ChromaDB nu are "list all" direct; folosim get() cu ids
            results = collection.get()
            if not results or not results.get("ids"):
                return []
            entries = []
            for i, doc_id in enumerate(results["ids"][-limit:]):
                entries.append(MemoryEntry(
                    id=doc_id,
                    agent_role=agent_role,
                    key=results["metadatas"][i].get("key", doc_id) if results.get("metadatas") else doc_id,
                    content=results["documents"][i] if results.get("documents") else "",
                    metadata=results["metadatas"][i] if results.get("metadatas") else {},
                    created_at=results["metadatas"][i].get("created_at", "") if results.get("metadatas") else "",
                    source="chromadb",
                ))
            log.debug("chroma.list: role=%r → %d entries", agent_role, len(entries))
            return entries
        except Exception as exc:
            log.warning("ChromaDB list() error: %s", exc)
            return []

    async def _sync_last_write_to_redis(self) -> None:
        """Update Redis last-write timestamp for chromadb tier.

        Synchronous write — fails if Redis is down but does not block
        the main ChromaDB write. Silent failure on Redis errors.
        """
        try:
            from memory.working.redis_backend import RedisBackend
            redis = RedisBackend()
            r = await redis._get_redis()
            iso_now = datetime.now(timezone.utc).isoformat()
            await r.set("goat2:working:last_write:chromadb", iso_now)  # type: ignore[union-attr]
            await redis.close()
            log.debug("Redis last_write:chromadb updated to %s", iso_now)
        except Exception as exc:
            log.debug("Redis last-write sync failed (non-blocking): %s", exc)

    async def retrieve(
        self, agent_role: AgentRole, key: MemoryKey,
    ) -> MemoryEntry | None:
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
        """Protocol read path: retrieve by key and bump access stats.

        Returns the entry (same as ``retrieve``) and, on a hit, best-effort
        increments ``access_count`` and refreshes ``accessed_at_ts`` via a
        re-upsert. ``retrieve`` stays pure (no write) for hot internal paths.
        """
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
