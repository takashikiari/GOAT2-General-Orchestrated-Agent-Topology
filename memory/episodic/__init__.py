"""Episodic memory layer — ChromaDB semantic storage.

Provides medium-term persistent memory with semantic search.
Used for past conversations, session histories, behavioral patterns.

EXPORTS:
- ChromaMemoryClient: Main ChromaDB-backed episodic memory client
- EpisodicMemoryBackend: Storage-neutral backend Protocol (swap any backend)
"""
from __future__ import annotations

import logging

from memory.episodic.chromadb_client import ChromaMemoryClient
from memory.episodic.backend_protocol import EpisodicMemoryBackend

log = logging.getLogger("goat2.memory.chroma")

__all__ = [
    "ChromaMemoryClient",
    "EpisodicMemoryBackend",
]