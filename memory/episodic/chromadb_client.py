"""ChromaMemoryClient — Episodic memory backend using embedded ChromaDB.

One persistent collection per GOAT agent role. Upsert semantics:
each (role, key) maps to exactly one document via a deterministic ID.
Semantic search uses HNSW (cosine space).

Inheritance chain: ChromaMemoryClient → ChromaCrudMixin → ChromaBase
All methods (store, retrieve, get, delete, search, list, clear, health,
count, collections, get_embedding) come from ChromaCrudMixin.
"""
from __future__ import annotations

import logging

from memory.episodic.chroma_crud import ChromaCrudMixin

log = logging.getLogger("goat2.memory.chroma")

__all__ = ["ChromaMemoryClient"]


class ChromaMemoryClient(ChromaCrudMixin):
    """Episodic memory backend using embedded ChromaDB.

    Implements EpisodicMemoryBackend Protocol structurally.
    No extra logic here — all operations delegated to ChromaCrudMixin.
    """
