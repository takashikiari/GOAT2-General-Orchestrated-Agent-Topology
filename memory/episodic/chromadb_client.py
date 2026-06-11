"""ChromaMemoryClient — Episodic memory backend using embedded ChromaDB.

One persistent collection per GOAT agent role. Upsert semantics:
each (role, key) maps to exactly one document via a deterministic ID.
Semantic search uses HNSW (cosine space).
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.memory.chroma")

from memory.episodic.chroma_crud import ChromaCrudMixin
from memory.episodic.chroma_extras import ChromaExtrasMixin
from memory.episodic.chroma_query import ChromaQueryMixin

__all__ = ["ChromaMemoryClient"]


class ChromaMemoryClient(ChromaCrudMixin, ChromaQueryMixin, ChromaExtrasMixin):
    """
    Episodic memory backend using embedded ChromaDB.

    One persistent collection per GOAT agent role. Upsert semantics:
    each (role, key) maps to exactly one document via a deterministic ID.
    Semantic search uses HNSW (cosine space).

    Mixins provide:
    - ChromaCrudMixin: store, retrieve, delete
    - ChromaQueryMixin: search, list, clear, health
    - ChromaExtrasMixin: count, introspection
    """
