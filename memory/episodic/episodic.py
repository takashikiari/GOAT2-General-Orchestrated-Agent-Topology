"""
memory.episodic.episodic — cross-session semantic memory backed by ChromaDB.

EpisodicMemory IS the ChromaDB implementation — no abstract backend layer.
'Episodic' means: persists across sessions, searchable by semantic similarity.
ChromaDB's API is synchronous; calls use asyncio.to_thread to avoid blocking.
"""
from __future__ import annotations

import asyncio
import uuid

from memory.config import EPISODIC_COLLECTION_NAME, EPISODIC_STORAGE_PATH
from utils.logging.setup import get_logger

log = get_logger(__name__)


class EpisodicMemory:
    """
    Cross-session conversation memory with semantic search.

    The ChromaDB client and collection are built lazily on first use so
    importing this class never touches the filesystem.  One instance is
    shared across all chat sessions (via ServiceRegistry).
    """

    def __init__(self) -> None:
        """Initialise with no connection — built on first use."""
        self._collection = None

    def _get_collection(self):
        """Return (and lazily create) the ChromaDB collection."""
        if self._collection is None:
            import chromadb  # lazy — avoids import-time filesystem access
            import posthog as _posthog
            from chromadb.config import Settings
            # posthog 7.7.0 disabled stub has wrong signature; patch before
            # PersistentClient() triggers telemetry calls.
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
        """
        Store one piece of content with metadata.

        Args:
            chat_id:  Conversation identifier; merged into stored metadata.
            content:  Text to embed and store.
            metadata: Caller-supplied metadata (e.g. {"role": "user", "ts": ...}).
        """
        merged = {"chat_id": chat_id, **metadata}
        doc_id = str(uuid.uuid4())
        await asyncio.to_thread(
            self._get_collection().add,
            ids=[doc_id],
            documents=[content],
            metadatas=[merged],
        )
        log.debug("EpisodicMemory: stored entry chat=%s id=%s", chat_id, doc_id)

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """
        Semantic search across all stored episodic entries.

        Args:
            query: Natural-language query; embedded and compared against stored entries.
            limit: Maximum number of results to return.

        Returns:
            List of {"content": str, "metadata": dict} dicts, closest first.
        """
        results = await asyncio.to_thread(
            self._get_collection().query,
            query_texts=[query],
            n_results=limit,
        )
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        return [{"content": d, "metadata": m} for d, m in zip(docs, metas)]
