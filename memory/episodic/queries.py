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
import re
import time

from utils.logging.setup import get_logger

log = get_logger(__name__)

# Detects UUID-form keys for the specific-key mechanism's get-by-id path.
# Local copy (not imported from query_classifier) so episodic stays free of any
# prefetch/orchestrator dependency.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I,
)


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

    async def find_by_keys(
        self, chat_id: str, keys: list[str], limit: int = 15,
    ) -> list[dict]:
        """Specific-key retrieval: exact structural matches, scoped to ``chat_id``.

        UUID-form keys are looked up by ChromaDB id (``get(ids=...)``); other
        structural keys (word+number, turn_/goat:) are matched as content
        substrings via ``where_document $contains`` (``$or`` when several).
        Results carry ``score = 0.0`` so the merger treats them as exact matches
        (similarity 1.0). Pure structural lookup — no semantic search.
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

        Reads current metadata for ``ids`` (scoped to ``chat_id``), increments
        ``access_count`` and stamps ``last_accessed_ts``, and rewrites the full
        metadata dict (merge-safe regardless of Chroma update semantics). Called
        fire-and-forget by the prefetch daemon; failure is logged at DEBUG and
        swallowed so a Chroma hiccup never affects the turn.
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
            await asyncio.to_thread(_sync)
        except Exception as exc:                    # noqa: BLE001 — best-effort, never fatal
            log.debug("bump_access failed chat=%s: %s", chat_id, exc)

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