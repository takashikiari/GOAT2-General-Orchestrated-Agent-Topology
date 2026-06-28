"""memory.episodic.queries — bulk read/delete on the episodic collection.

Split from ``episodic.py`` for the file-size rule: core lifecycle + store/search
live there; these admin/bulk-query methods live here as a mixin so
``EpisodicMemory`` stays one public type and callers keep using
``episodic.get_recent / count / get_oldest / delete_entries`` unchanged. All
access the lazily-built collection via ``self._get_collection`` (defined on the
core class) and bridge ChromaDB's sync API with ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio

from utils.logging.setup import get_logger

log = get_logger(__name__)


class EpisodicQueries:
    """Bulk read/delete mixin for ``EpisodicMemory`` (uses ``self._get_collection``)."""

    async def get_recent(self, chat_id: str, limit: int = 20) -> list[dict]:
        """Most recent N entries for chat_id in chronological order."""
        results = await asyncio.to_thread(
            self._get_collection().get,
            where={"chat_id": chat_id}, include=["documents", "metadatas"],
        )
        entries = sorted(
            [{"content": d, "metadata": m}
             for d, m in zip(results["documents"] or [], results["metadatas"] or [])],
            key=lambda e: float(e["metadata"].get("timestamp", 0)),
        )
        return entries[-limit:]

    async def count(self, chat_id: str | None = None) -> int:
        """Return total entry count (global) or filtered by chat_id."""
        if chat_id is None:
            return await asyncio.to_thread(self._get_collection().count)
        r = await asyncio.to_thread(
            self._get_collection().get, where={"chat_id": chat_id}, include=["metadatas"],
        )
        return len(r["ids"])

    async def get_oldest(self, limit: int, chat_id: str | None = None) -> list[dict]:
        """Return oldest N entries (timestamp asc). Each entry includes 'id' for deletion."""
        kwargs: dict = {"include": ["documents", "metadatas"]}
        if chat_id is not None:
            kwargs["where"] = {"chat_id": chat_id}
        results = await asyncio.to_thread(self._get_collection().get, **kwargs)
        all_e = [{"id": i, "content": d, "metadata": m}
                 for i, d, m in zip(results["ids"], results["documents"], results["metadatas"])]
        return sorted(all_e, key=lambda e: float(e["metadata"].get("timestamp", 0)))[:limit]

    async def delete_entries(self, entry_ids: list[str]) -> None:
        """Delete entries by their ChromaDB document IDs."""
        if not entry_ids:
            return
        await asyncio.to_thread(self._get_collection().delete, ids=entry_ids)
        log.debug("EpisodicMemory: deleted %d entries", len(entry_ids))