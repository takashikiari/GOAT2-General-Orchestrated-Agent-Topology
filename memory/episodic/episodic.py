"""memory.episodic.episodic — cross-session memory backed by ChromaDB. Sync API; asyncio.to_thread."""
from __future__ import annotations

import asyncio
import uuid

from memory.config import EPISODIC_COLLECTION_NAME, EPISODIC_STORAGE_PATH
from utils.logging.setup import get_logger

log = get_logger(__name__)


class EpisodicMemory:
    """Cross-session memory: semantic search, recency, and bulk ops. Lazily connected."""

    def __init__(self) -> None:
        self._collection = None

    def _get_collection(self):
        if self._collection is None:
            import chromadb
            import posthog as _posthog
            from chromadb.config import Settings
            _posthog.disabled = True
            _posthog.capture = lambda *args, **kwargs: None  # type: ignore[assignment]
            client = chromadb.PersistentClient(
                path=EPISODIC_STORAGE_PATH,
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(EPISODIC_COLLECTION_NAME)
            log.debug("EpisodicMemory: collection ready (%s)", EPISODIC_COLLECTION_NAME)
        return self._collection

    async def store(self, chat_id: str, content: str, metadata: dict) -> None:
        """Store content + metadata. chat_id merged into metadata."""
        merged = {"chat_id": chat_id, **metadata}
        doc_id = str(uuid.uuid4())
        await asyncio.to_thread(
            self._get_collection().add,
            ids=[doc_id], documents=[content], metadatas=[merged],
        )

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search. Returns {"content", "metadata"} dicts, closest first."""
        results = await asyncio.to_thread(
            self._get_collection().query, query_texts=[query], n_results=limit,
        )
        docs, metas = results["documents"][0], results["metadatas"][0]
        return [{"content": d, "metadata": m} for d, m in zip(docs, metas)]

    async def get_recent(self, chat_id: str, limit: int = 20) -> list[dict]:
        """Most recent N entries for chat_id in chronological order."""
        results = await asyncio.to_thread(
            self._get_collection().get,
            where={"chat_id": chat_id}, include=["documents", "metadatas"],
        )
        entries = sorted(
            [{"content": d, "metadata": m}
             for d, m in zip(results["documents"] or [], results["metadatas"] or [])],
            key=lambda e: float(e["metadata"].get("timestamp", 0)),
        )
        return entries[-limit:]

    async def count(self, chat_id: str | None = None) -> int:
        """Return total entry count (global) or filtered by chat_id."""
        if chat_id is None:
            return await asyncio.to_thread(self._get_collection().count)
        r = await asyncio.to_thread(
            self._get_collection().get, where={"chat_id": chat_id}, include=["metadatas"],
        )
        return len(r["ids"])

    async def get_oldest(self, limit: int, chat_id: str | None = None) -> list[dict]:
        """Return oldest N entries (timestamp asc). Each entry includes 'id' for deletion."""
        kwargs: dict = {"include": ["documents", "metadatas"]}
        if chat_id is not None:
            kwargs["where"] = {"chat_id": chat_id}
        results = await asyncio.to_thread(self._get_collection().get, **kwargs)
        all_e = [{"id": i, "content": d, "metadata": m}
                 for i, d, m in zip(results["ids"], results["documents"], results["metadatas"])]
        return sorted(all_e, key=lambda e: float(e["metadata"].get("timestamp", 0)))[:limit]

    async def delete_entries(self, entry_ids: list[str]) -> None:
        """Delete entries by their ChromaDB document IDs."""
        if not entry_ids:
            return
        await asyncio.to_thread(self._get_collection().delete, ids=entry_ids)
        log.debug("EpisodicMemory: deleted %d entries", len(entry_ids))
