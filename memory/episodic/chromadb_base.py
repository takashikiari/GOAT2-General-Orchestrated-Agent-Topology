from __future__ import annotations

import logging
import os

import chromadb
import chromadb.errors

from memory.episodic.chroma_helpers import _collection_name
from memory.episodic.chroma_types import _ChromaCollectionConfig, _DEFAULT_PERSIST_DIR, _HNSW_SPACE
from memory.shared.types import AgentRole

log = logging.getLogger("goat2.memory.chroma")
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)


class ChromaBase:
    """Manages the ChromaDB client and per-role collection handles."""

    __slots__ = ("_persist_dir", "_embedding_fn", "_chroma", "_cols")

    def __init__(
        self,
        persist_dir:  str | None = None,
        embedding_fn: object | None = None,   # chromadb EmbeddingFunction — opaque PyO3 object
    ) -> None:
        self._persist_dir: str = (
            persist_dir
            or os.environ.get("CHROMA_PERSIST_DIR", "")
            or _DEFAULT_PERSIST_DIR
        )
        self._embedding_fn = embedding_fn
        self._chroma: chromadb.ClientAPI | None            = None
        self._cols:   dict[AgentRole, chromadb.Collection] = {}
        log.debug(
            "ChromaBase: initialised (persist_dir=%s has_embedding=%s)",
            self._persist_dir, self._embedding_fn is not None,
        )

    def _get_chroma(self) -> chromadb.ClientAPI:
        """Lazily initialise the ChromaDB PersistentClient, creating the persist dir if needed."""
        if self._chroma is None:
            os.makedirs(self._persist_dir, exist_ok=True)
            self._chroma = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=chromadb.Settings(anonymized_telemetry=False),
                tenant="default_tenant",
                database="default_database",
            )
            log.debug("ChromaDB initialised at %s", self._persist_dir)
        return self._chroma

    def _get_collection(self, role: AgentRole) -> chromadb.Collection:
        """Return (or create) the cosine-HNSW collection for role, cached per instance."""
        if role not in self._cols:
            client = self._get_chroma()
            kwargs: _ChromaCollectionConfig = {
                "name": _collection_name(role),
                "metadata": {
                    "hnsw:space": _HNSW_SPACE,
                    "description": f"GOAT 2.0 {role} episodic memory",
                },
            }
            if self._embedding_fn is not None:
                kwargs["embedding_function"] = self._embedding_fn
            self._cols[role] = client.get_or_create_collection(**kwargs)
        return self._cols[role]
