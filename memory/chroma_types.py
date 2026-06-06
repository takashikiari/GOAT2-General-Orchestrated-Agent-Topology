from __future__ import annotations

import os
from typing import Final, NotRequired, Required, TypedDict

__all__ = [
    "ChromaStoredMetadata", "ChromaGetResult", "ChromaQueryResult",
    "_ChromaCollectionConfig",
    "_DEFAULT_PERSIST_DIR", "_COLLECTION_PREFIX", "_HNSW_SPACE",
    "_LIST_FETCH_MAX", "_SEARCH_TAG_OVERSAMPLE",
]

_DEFAULT_PERSIST_DIR: Final[str] = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "chroma_db")
)
_COLLECTION_PREFIX:     Final[str] = "goat2_"
_HNSW_SPACE:            Final[str] = "cosine"
_LIST_FETCH_MAX:        Final[int] = 1_000
_SEARCH_TAG_OVERSAMPLE: Final[int] = 3


class ChromaStoredMetadata(TypedDict):
    """Scalar metadata stored alongside every ChromaDB document."""
    agent_role:    str
    key:           str
    created_at:    str
    created_at_ts: int
    tags:          str    # comma-separated


class ChromaGetResult(TypedDict, total=False):
    """Shape of chromadb Collection.get() return value."""
    ids:        Required[list[str]]
    documents:  list[str | None]
    metadatas:  list[ChromaStoredMetadata | None]


class ChromaQueryResult(TypedDict, total=False):
    """Shape of chromadb Collection.query() return value."""
    ids:        Required[list[list[str]]]
    documents:  list[list[str | None]]
    metadatas:  list[list[ChromaStoredMetadata | None]]
    distances:  list[list[float]]


class _ChromaCollectionConfig(TypedDict, total=False):
    """Typed kwargs passed to chromadb.get_or_create_collection(). Replaces dict[str, Any]."""
    name:               str
    metadata:           dict[str, str]
    embedding_function: object          # chromadb EmbeddingFunction — opaque PyO3 object
