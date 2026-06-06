from __future__ import annotations

import asyncio
import logging
from typing import Final

from memory.chroma_helpers import (
    _build_chroma_metadata, _doc_id, _now_iso, _now_ts, _str_to_tags,
)
from memory.chroma_parsers import _parse_get_result
from memory.chroma_types import ChromaGetResult
from memory.chromadb_base import ChromaBase
from memory.types import AgentRole, IsoTimestamp, MemoryEntry, MemoryEntryMetadata, MemoryKey

log = logging.getLogger("goat2.memory.chroma")
_SOURCE: Final[str] = "chroma"


class ChromaCrudMixin(ChromaBase):
    """store, retrieve, delete for ChromaMemoryClient."""

    async def store(
        self, agent_role: AgentRole, key: MemoryKey, content: str,
        *, metadata: MemoryEntryMetadata | None = None, ttl: int | None = None,
    ) -> MemoryEntry:
        ts     = _now_ts()
        iso    = _now_iso()
        meta   = _build_chroma_metadata(agent_role, key, metadata, ts, iso)
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

        return MemoryEntry(
            id=doc_id, agent_role=agent_role, key=key, content=content,
            metadata=MemoryEntryMetadata(
                tags=_str_to_tags(meta["tags"]), created_at_ts=float(ts),
            ),
            created_at=iso, source=_SOURCE,
        )

    async def retrieve(
        self, agent_role: AgentRole, key: MemoryKey,
    ) -> MemoryEntry | None:
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

    async def delete(self, agent_role: AgentRole, key: MemoryKey) -> bool:
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
