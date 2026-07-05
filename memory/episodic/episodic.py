"""memory.episodic.episodic — cross-session memory backed by ChromaDB. Sync API; asyncio.to_thread.

Core lifecycle + store/search live here; bulk read/delete (get_recent/count/
get_oldest/delete_entries) live in ``memory.episodic.queries`` and are mixed in,
to keep this module within the file-size limit. Lazily connected.

``_write_lock`` serialises all mutating ChromaDB calls (``col.add``,
``col.update``, ``col.delete``) so concurrent async callers sharing one
EpisodicMemory instance never race on the HNSW index.  Read-only calls
(``col.query``, ``col.get``) are unaffected — hnswlib allows concurrent reads.
"""
from __future__ import annotations

import asyncio
import uuid

from memory.config import EPISODIC_COLLECTION_NAME, EPISODIC_STORAGE_PATH
from memory.episodic.queries import EpisodicQueries
from memory.episodic.warmup import warmup_collection
from utils.logging.setup import get_logger

log = get_logger(__name__)


class EpisodicMemory(EpisodicQueries):
    """Cross-session memory: semantic search + recency + bulk ops. Lazily connected.

    Bulk read/delete come from the ``EpisodicQueries`` mixin; this class owns the
    collection lifecycle, warmup, store, and semantic search.
    """

    def __init__(self) -> None:
        self._collection = None
        self._write_lock = asyncio.Lock()

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
        await warmup_collection(self._get_collection)

    async def store(self, chat_id: str, content: str, metadata: dict) -> None:
        """Store content + metadata under the write lock.

        ``access_count`` and ``last_accessed_ts`` initialised so merge-score
        terms exist from the first write; ``bump_access`` updates them on
        retrieval (best-effort, fire-and-forget).
        """
        doc_id = str(uuid.uuid4())
        merged = {"chat_id": chat_id, **metadata}
        merged.setdefault("access_count", 0)
        merged.setdefault("last_accessed_ts", merged.get("timestamp", 0.0))

        def _sync() -> None:
            col = self._get_collection()
            merged["message_id"] = doc_id
            merged["sequence_number"] = col.count() + 1
            col.add(ids=[doc_id], documents=[content], metadatas=[merged])

        async with self._write_lock:
            await asyncio.to_thread(_sync)
        log.debug("L3 write ok: chat=%s doc_id=%s tags=%r", chat_id, doc_id, metadata.get("tags", ""))

    async def search(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
    ) -> list[dict]:
        """Semantic search with optional timestamp filter (read-only, no lock).

        Returns ``{"content", "metadata", "score"}`` dicts, closest first.
        ``score`` is ChromaDB's distance (lower = closer under the default L2
        metric).  A custom collection that omits distances degrades to
        ``score = 0.0`` rather than crashing the turn.
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
        if len(dists) != len(docs):
            dists = [0.0] * len(docs)
        return [
            {"content": d, "metadata": m, "score": s}
            for d, m, s in zip(docs, metas, dists)
        ]

    async def embed_query(self, query: str) -> list[float] | None:
        """Embed ``query`` via the collection's own embedding function.

        Reuses the same model the semantic search uses (the collection's
        bundled ONNX MiniLM by default).  Any failure degrades to ``None``
        rather than raising — callers treat ``None`` as "force a cold turn".
        """
        if not query:
            return None

        def _sync() -> list[float] | None:
            col = self._get_collection()
            ef = getattr(col, "_embedding_function", None)
            if ef is None:
                return None
            return [float(x) for x in ef([query])[0]]

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            log.warning("embed_query failed, degrading to cold turn: %s", exc)
            return None
