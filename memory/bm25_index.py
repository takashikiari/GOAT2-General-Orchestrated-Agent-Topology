"""memory.bm25_index — registry-owned BM25 lexical index for hybrid retrieval.

Complements ChromaDB semantic search with keyword/lexical recall so queries
containing specific names, dates, or rare terms surface relevant memories even
when the bi-encoder similarity is low. Loaded lazily from all ChromaDB documents
on first search; updated incrementally on every L3 write.

Results carry a ``bm25_score`` field but intentionally omit ``score`` (Chroma L2
distance) so ``result_merger.merge_results`` treats them as zero-similarity
candidates — the recency and access-count terms still apply, and the
cross-encoder reranker makes the final relevance call.
"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.episodic import EpisodicMemory

log = get_logger(__name__)


class BM25Index:
    """Registry-owned BM25 text index; lazily built from ChromaDB on first search."""

    def __init__(self, episodic: "EpisodicMemory") -> None:
        """Store episodic reference; index built lazily on first search or warmup."""
        self._episodic = episodic
        self._bm25 = None           # BM25Okapi — set after first build
        self._docs: list[dict] = []
        self._lock = asyncio.Lock()

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Lowercase + strip punctuation so 'demisia.' matches query 'demisie'."""
        return re.sub(r"[^\w\s]", " ", text.lower()).split()

    async def warmup(self) -> None:
        """Pre-build the index at startup to avoid first-turn build latency."""
        async with self._lock:
            if self._bm25 is None:
                await self._build()

    async def _build(self) -> None:
        """Load all documents from ChromaDB and construct BM25Okapi. Called under lock."""
        from rank_bm25 import BM25Okapi  # lazy — optional dep
        all_docs = await self._episodic.get_all_for_index()
        self._docs = [
            {"id": d["id"], "content": d["content"], "metadata": d["metadata"]}
            for d in all_docs
        ]
        corpus = [self._tokenize(d["content"]) for d in self._docs]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        log.info("BM25Index: built from %d documents", len(self._docs))

    def add_doc(self, doc_id: str, content: str, metadata: dict) -> None:
        """Append a document and rebuild the index; sync, cheap at corpus < 10K docs."""
        from rank_bm25 import BM25Okapi  # lazy
        self._docs.append({"id": doc_id, "content": content, "metadata": metadata})
        if self._bm25 is not None:
            self._bm25 = BM25Okapi([self._tokenize(d["content"]) for d in self._docs])

    async def search(self, query: str, limit: int = 15) -> list[dict]:
        """BM25 search; returns dicts with bm25_score, positive-score hits only.

        Results intentionally lack a ``score`` key so ``result_merger`` assigns
        zero semantic similarity — the recency/access terms still fire, and the
        cross-encoder makes the final call on relevance.
        """
        async with self._lock:
            if self._bm25 is None:
                await self._build()
        if not self._docs or self._bm25 is None:
            return []
        tokens = self._tokenize(query)
        docs_snap, bm25_snap = list(self._docs), self._bm25

        def _score() -> list[dict]:
            scores = bm25_snap.get_scores(tokens)
            hits = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:limit]
            return [
                {"content": docs_snap[i]["content"], "metadata": docs_snap[i]["metadata"],
                 "bm25_score": float(s)}
                for i, s in hits if s > 0
            ]

        return await asyncio.to_thread(_score)
