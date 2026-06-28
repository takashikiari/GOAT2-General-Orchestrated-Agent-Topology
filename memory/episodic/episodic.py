"""memory.episodic.episodic — cross-session memory backed by ChromaDB. Sync API; asyncio.to_thread.

Core lifecycle + store/search live here; bulk read/delete (get_recent/count/
get_oldest/delete_entries) live in ``memory.episodic.queries`` and are mixed in,
to keep this module within the file-size limit. Lazily connected.
"""
from __future__ import annotations

import asyncio
import uuid

from memory.config import EPISODIC_COLLECTION_NAME, EPISODIC_STORAGE_PATH
from memory.episodic.queries import EpisodicQueries
from utils.logging.setup import get_logger

log = get_logger(__name__)


class EpisodicMemory(EpisodicQueries):
    """Cross-session memory: semantic search + recency + bulk ops. Lazily connected.

    Bulk read/delete come from the ``EpisodicQueries`` mixin; this class owns the
    collection lifecycle, warmup, store, and semantic search.
    """

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

    async def warmup(self) -> None:
        """Pre-warm the collection at startup (delegates to ``episodic.warmup``)."""
        from memory.episodic.warmup import warmup_collection
        await warmup_collection(self._get_collection)

    async def store(self, chat_id: str, content: str, metadata: dict) -> None:
        """Store content + metadata. chat_id, message_id, sequence_number merged."""
        doc_id = str(uuid.uuid4())
        merged = {"chat_id": chat_id, **metadata}

        def _sync() -> None:
            col = self._get_collection()
            merged["message_id"] = doc_id
            merged["sequence_number"] = col.count() + 1
            col.add(ids=[doc_id], documents=[content], metadatas=[merged])

        await asyncio.to_thread(_sync)

    async def search(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
    ) -> list[dict]:
        """Semantic search with optional timestamp filter.

        Returns ``{"content", "metadata", "score"}`` dicts, closest first.
        ``score`` is ChromaDB's distance (lower = closer under the default L2
        metric; the collection has no ``hnsw:space`` override, so squared-L2).
        Callers use it to similarity-filter L3 injection. A custom collection
        that omits distances degrades to ``score = 0.0`` (passes any filter)
        rather than crashing the turn.
        """
        where: dict | None = None
        if after is not None and before is not None:
            where = {"$and": [{"timestamp": {"$gte": after}}, {"timestamp": {"$lte": before}}]}
        elif after is not None:
            where = {"timestamp": {"$gte": after}}
        elif before is not None:
            where = {"timestamp": {"$lte": before}}
        kw: dict = {"query_texts": [query], "n_results": limit}
        if where:
            kw["where"] = where
        results = await asyncio.to_thread(self._get_collection().query, **kw)
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results.get("distances", [[]])[0]
        if len(dists) != len(docs):                      # defensive: degrade to 0.0
            dists = [0.0] * len(docs)
        return [
            {"content": d, "metadata": m, "score": s}
            for d, m, s in zip(docs, metas, dists)
        ]