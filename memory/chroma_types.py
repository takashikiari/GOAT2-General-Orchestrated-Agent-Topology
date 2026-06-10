"""Backward compatibility shim for memory.chroma_types.

This module has been moved to memory.episodic.chroma_types.
"""
from memory.episodic.chroma_types import (
    _DEFAULT_PERSIST_DIR,
    _COLLECTION_PREFIX,
    _HNSW_SPACE,
    _ChromaCollectionConfig,
    ChromaGetResult,
    ChromaQueryResult,
    ChromaStoredMetadata,
)

__all__ = [
    "_DEFAULT_PERSIST_DIR",
    "_COLLECTION_PREFIX",
    "_HNSW_SPACE",
    "_ChromaCollectionConfig",
    "ChromaGetResult",
    "ChromaQueryResult",
    "ChromaStoredMetadata",
]