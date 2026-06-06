from __future__ import annotations

from memory.chroma_crud import ChromaCrudMixin
from memory.chroma_extras import ChromaExtrasMixin
from memory.chroma_query import ChromaQueryMixin

__all__ = ["ChromaMemoryClient", "chroma_client"]


class ChromaMemoryClient(ChromaCrudMixin, ChromaQueryMixin, ChromaExtrasMixin):
    """
    Episodic memory backend using embedded ChromaDB.

    One persistent collection per GOAT agent role.  Upsert semantics:
    each (role, key) maps to exactly one document via a deterministic ID.
    Semantic search uses HNSW (cosine space).
    """


chroma_client = ChromaMemoryClient()
