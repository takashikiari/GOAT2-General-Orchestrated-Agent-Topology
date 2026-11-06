"""memory.episodic.chunks — Phase 1 of memory spec v4: L3 entries split into ~150-300-token chunks.

Why: the cross-encoder scores (query, candidate) pairs in one padded batch, so a single long L3 entry
(median ~1000 tokens, capped at 512) made every rerank cost ~4.5 s, and long entries also filled the
prompt with text the reply never used (Phase 0: ~30 % of L3 tokens unused).

Design (agreed with GOAT/Laris, spec v4):
- A SEPARATE Chroma store (own directory + collection). The original collection stays the source of
  truth for writes, enrichment and every maintenance query; rollback is ``chunks_search = false``.
- Chunks are word windows of ~CHUNK_TARGET tokens with CHUNK_OVERLAP tokens of overlap; each chunk keeps
  the parent's scalar metadata (topic_id, entities, sequence_number, chat_id, timestamp, message_id …)
  plus ``parent_id``/``chunk_index``/``n_chunks``. ``message_id`` stays the parent id, so the existing
  merge (deduped by message_id) keeps one chunk per parent.
- After rerank, ``expand`` replaces a chunk by its ±1 neighbour window, rebuilt from the parent text by
  re-chunking it (deterministic), so overlap text is never duplicated.
"""
from __future__ import annotations

import asyncio

from memory.budget import estimate_tokens
from memory.config import (
    CHUNK_COLLECTION_NAME, CHUNK_OVERLAP_TOKENS, CHUNK_STORAGE_PATH, CHUNK_TARGET_TOKENS,
)
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["chunk_spans", "chunk_records", "window_text", "ChunkStore"]

_SCALARS = (str, int, float, bool)


def chunk_spans(text: str, target: int = CHUNK_TARGET_TOKENS, overlap: int = CHUNK_OVERLAP_TOKENS) -> list[tuple[int, int]]:
    """Word-index spans [start, end) of ~``target`` tokens, consecutive spans overlapping by ~``overlap``."""
    words = (text or "").split()
    if not words:
        return [(0, 0)]
    costs = [estimate_tokens(w) + 1 for w in words]
    spans, start = [], 0
    while start < len(words):
        end, total = start, 0
        while end < len(words) and (total + costs[end] <= target or end == start):
            total += costs[end]
            end += 1
        spans.append((start, end))
        if end >= len(words):
            break
        back, nxt = 0, end
        while nxt > start + 1 and back + costs[nxt - 1] <= overlap:
            nxt -= 1
            back += costs[nxt]
        start = nxt
    return spans


def chunk_records(doc_id: str, content: str, meta: dict) -> list[tuple[str, str, dict]]:
    """(chunk_id, text, metadata) for every chunk of one parent entry."""
    words = (content or "").split()
    spans = chunk_spans(content)
    base = {k: v for k, v in (meta or {}).items() if isinstance(v, _SCALARS)}
    base["message_id"] = base.get("message_id") or doc_id
    out = []
    for i, (a, b) in enumerate(spans):
        m = dict(base, parent_id=doc_id, chunk_index=i, n_chunks=len(spans))
        out.append((f"{doc_id}#{i}", " ".join(words[a:b]) if words else "", m))
    return out


def window_text(content: str, chunk_index: int, radius: int = 1) -> str:
    """Text of chunk ``chunk_index`` with ``radius`` neighbours on each side, rebuilt from the parent."""
    words = (content or "").split()
    spans = chunk_spans(content)
    i = max(0, min(int(chunk_index), len(spans) - 1))
    a = spans[max(0, i - radius)][0]
    b = spans[min(len(spans) - 1, i + radius)][1]
    return " ".join(words[a:b])


class ChunkStore:
    """Second Chroma store holding the chunks. Sync Chroma calls run in threads under ``lock``."""

    def __init__(self, path: str | None = None, collection: str | None = None, lock: asyncio.Lock | None = None,
                 embedding_function=None) -> None:
        self._path = path or CHUNK_STORAGE_PATH
        self._name = collection or CHUNK_COLLECTION_NAME
        self._col = None
        self._ef = embedding_function  # None = Chroma's default, the same one the source collection uses
        self.lock = lock or asyncio.Lock()

    def collection(self):
        if self._col is None:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.PersistentClient(path=self._path, settings=Settings(anonymized_telemetry=False))
            kw = {"embedding_function": self._ef} if self._ef is not None else {}
            self._col = client.get_or_create_collection(self._name, **kw)
        return self._col

    def add_sync(self, doc_id: str, content: str, meta: dict) -> int:
        recs = chunk_records(doc_id, content, meta)
        self.collection().upsert(ids=[r[0] for r in recs], documents=[r[1] for r in recs],
                                 metadatas=[r[2] for r in recs])
        return len(recs)

    def update_sync(self, doc_id: str, updates: dict) -> None:
        col = self.collection()
        r = col.get(where={"parent_id": {"$eq": doc_id}}, include=["metadatas"])
        ids, metas = r.get("ids") or [], r.get("metadatas") or []
        if ids:
            clean = {k: v for k, v in updates.items() if isinstance(v, _SCALARS)}
            col.update(ids=ids, metadatas=[dict(m or {}, **clean) for m in metas])

    def delete_sync(self, doc_ids: list[str]) -> None:
        if doc_ids:
            self.collection().delete(where={"parent_id": {"$in": list(doc_ids)}})

    def parent_ids_sync(self) -> set[str]:
        metas = self.collection().get(include=["metadatas"]).get("metadatas") or []
        return {m.get("parent_id") for m in metas if m and m.get("parent_id")}

    def all_for_index_sync(self) -> list[dict]:
        r = self.collection().get(include=["documents", "metadatas"])
        return [{"id": i, "content": d, "metadata": m}
                for i, d, m in zip(r.get("ids") or [], r.get("documents") or [], r.get("metadatas") or [])]

    def query_sync(self, **kw):
        return self.collection().query(**kw)
