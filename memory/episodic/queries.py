"""memory.episodic.queries — bulk read/delete on the episodic collection.

Split from ``episodic.py`` for the file-size rule: core lifecycle + store/search
live there; these admin/bulk-query methods live here as a mixin so
``EpisodicMemory`` stays one public type and callers keep using
``episodic.get_recent / count / get_oldest / delete_entries`` unchanged. All
access the lazily-built collection via ``self._get_collection`` (defined on the
core class) and bridge ChromaDB's sync API with ``asyncio.to_thread``.

Write operations (``bump_access``, ``delete_entries``) hold ``self._write_lock``
(set by ``EpisodicMemory.__init__``) to prevent concurrent writes from racing on
the HNSW index.
"""
from __future__ import annotations

import asyncio
import re
import time

from utils.logging.setup import get_logger

log = get_logger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I,
)


class EpisodicQueries:
    """Bulk read/delete mixin for ``EpisodicMemory`` (uses ``self._get_collection``)."""

    async def get_recent(self, chat_id: str, limit: int = 20) -> list[dict]:
        """Most recent N entries for chat_id in chronological order (read-only)."""
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

    async def find_by_keys(
        self, chat_id: str, keys: list[str], limit: int = 15,
    ) -> list[dict]:
        """Specific-key retrieval: exact structural matches, scoped to ``chat_id`` (read-only).

        UUID-form keys are looked up by ChromaDB id; other structural keys are
        matched as content substrings.  Results carry ``score = 0.0``.
        """
        if not keys:
            return []
        uuids = [k for k in keys if _UUID_RE.match(k)]
        content_keys = [k for k in keys if not _UUID_RE.match(k)]
        out: list[dict] = []

        def _by_ids() -> None:
            if not uuids:
                return
            r = self._get_collection().get(
                ids=uuids, where={"chat_id": chat_id},
                include=["documents", "metadatas"],
            )
            for i, d, m in zip(r.get("ids", []) or [], r.get("documents", []) or [],
                              r.get("metadatas", []) or []):
                meta = dict(m or {})
                meta.setdefault("message_id", i)
                out.append({"content": d, "metadata": meta, "score": 0.0})

        def _by_content() -> None:
            if not content_keys:
                return
            clauses = [{"$contains": k} for k in content_keys]
            where_doc = clauses[0] if len(clauses) == 1 else {"$or": clauses}
            r = self._get_collection().get(
                where={"chat_id": chat_id}, where_document=where_doc,
                include=["documents", "metadatas"], limit=limit,
            )
            for i, d, m in zip(r.get("ids", []) or [], r.get("documents", []) or [],
                              r.get("metadatas", []) or []):
                meta = dict(m or {})
                meta.setdefault("message_id", i)
                out.append({"content": d, "metadata": meta, "score": 0.0})

        await asyncio.to_thread(_by_ids)
        await asyncio.to_thread(_by_content)
        return out

    async def bump_access(self, chat_id: str, ids: list[str]) -> None:
        """Best-effort retrieval-popularity bump; never raises.

        Holds ``_write_lock`` around the read-modify-write so concurrent bumps
        on the same documents don't produce lost increments.
        """
        if not ids:
            return
        now = time.time()

        def _sync() -> None:
            col = self._get_collection()
            r = col.get(ids=ids, where={"chat_id": chat_id}, include=["metadatas"])
            out_ids: list[str] = []
            out_metas: list[dict] = []
            for i, m in zip(r.get("ids", []) or [], r.get("metadatas", []) or []):
                merged = dict(m or {})
                merged["access_count"] = int(merged.get("access_count", 0)) + 1
                merged["last_accessed_ts"] = now
                out_ids.append(i)
                out_metas.append(merged)
            if out_ids:
                col.update(ids=out_ids, metadatas=out_metas)

        try:
            async with self._write_lock:
                await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            log.debug("bump_access failed chat=%s: %s", chat_id, exc)

    async def count(self, chat_id: str | None = None) -> int:
        """Return total entry count (global) or filtered by chat_id (read-only)."""
        if chat_id is None:
            return await asyncio.to_thread(self._get_collection().count)
        r = await asyncio.to_thread(
            self._get_collection().get, where={"chat_id": chat_id}, include=["metadatas"],
        )
        return len(r["ids"])

    async def get_oldest(self, limit: int, chat_id: str | None = None) -> list[dict]:
        """Return oldest N entries (timestamp asc) (read-only)."""
        kwargs: dict = {"include": ["documents", "metadatas"]}
        if chat_id is not None:
            kwargs["where"] = {"chat_id": chat_id}
        results = await asyncio.to_thread(self._get_collection().get, **kwargs)
        all_e = [{"id": i, "content": d, "metadata": m}
                 for i, d, m in zip(results["ids"], results["documents"], results["metadatas"])]
        return sorted(all_e, key=lambda e: float(e["metadata"].get("timestamp", 0)))[:limit]

    async def delete_entries(self, entry_ids: list[str]) -> None:
        """Delete entries by their ChromaDB document IDs (write-locked)."""
        if not entry_ids:
            return
        async with self._write_lock:
            await asyncio.to_thread(self._get_collection().delete, ids=entry_ids)
        log.debug("EpisodicMemory: deleted %d entries", len(entry_ids))

    async def update_metadata(self, doc_id: str, updates: dict) -> None:
        """Update metadata fields on an existing L3 entry (write-locked).

        Merges ``updates`` into existing metadata so callers only specify the
        fields they want to change. Silently no-ops if ``doc_id`` is not found.
        """
        def _sync() -> None:
            col = self._get_collection()
            r = col.get(ids=[doc_id], include=["metadatas"])
            existing = dict((r.get("metadatas") or [{}])[0] or {})
            existing.update(updates)
            col.update(ids=[doc_id], metadatas=[existing])

        try:
            async with self._write_lock:
                await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            log.debug("update_metadata failed doc_id=%s: %s", doc_id, exc)
