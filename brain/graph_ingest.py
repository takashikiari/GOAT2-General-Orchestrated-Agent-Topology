"""brain.graph_ingest — feeds the memory graph from L3 on the brain's tick, in small slices (phase F1).

Each tick: read from Chroma (read-only, outside the brain lock) the metadata of the L3 memories at or after the
graph cursor, take the oldest ``limit`` of them, then write their nodes and links under the brain lock, in a thread,
and advance the cursor. ``>=`` at the cursor plus idempotent writes means a memory sharing the cursor's timestamp is
never skipped. The first ticks backfill the existing memories (3.8k at 200 per tick ≈ 20 minutes); after that each
tick only sees the new ones. Nothing reads the graph yet.
"""
from __future__ import annotations

import asyncio
import time

from brain import graph
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["pending_docs", "ingest_slice", "maintain_slice"]


def pending_docs(collection, since: float) -> list[dict]:
    """L3 memories with timestamp >= ``since``, metadata only (no text, no embeddings), oldest first."""
    got = collection.get(where={"timestamp": {"$gte": float(since)}}, include=["metadatas"])
    docs = []
    for cid, meta in zip(got.get("ids") or [], got.get("metadatas") or []):
        meta = meta or {}
        mid = meta.get("message_id") or cid
        docs.append({"id": str(mid), "ts": float(meta.get("timestamp") or 0), "chat_id": meta.get("chat_id"),
                     "topic_id": meta.get("topic_id"), "sequence_number": meta.get("sequence_number"),
                     "entities": meta.get("entities") or "", "entity_types": meta.get("entity_types") or ""})
    docs.sort(key=lambda d: d["ts"])
    return docs


async def ingest_slice(brain, episodic, limit: int = 200) -> dict:
    """One slice: up to ``limit`` memories into the graph. Returns what was added plus the graph's totals."""
    t0 = time.perf_counter()
    async with brain.lock:
        since = await asyncio.to_thread(graph.cursor, brain._db)
    collection = episodic._get_collection()
    docs = (await asyncio.to_thread(pending_docs, collection, since))[: int(limit)]
    if not docs:
        return {"docs": 0}
    now = time.time()

    def _write() -> dict:
        added = graph.ingest(brain._db, docs, now)
        graph.cursor(brain._db, value=docs[-1]["ts"])
        return {**added, **graph.counts(brain._db)}
    async with brain.lock:
        out = await asyncio.to_thread(_write)
    out["docs"], out["ms"] = len(docs), round((time.perf_counter() - t0) * 1000, 1)
    if out.get("episodes"):
        log.info("brain: graph ingest docs=%d new_episodes=%d new_things=%d despre=%d urmeaza=%d nodes=%d edges=%d "
                 "ms=%.1f", out["docs"], out["episodes"], out["things"], out["despre"], out["urmeaza"],
                 out["total_nodes"], out["total_edges"], out["ms"])
    return out


async def maintain_slice(brain, pairs_limit: int = 200, fade_limit: int = 500, hub_fraction: float = 0.15,
                         half_life_days: float = 14.0) -> dict:
    """The living part of sleep (design §12): link things met together (oldest episodes first, so the history is
    covered in slices), and bring the stalest link weights up to date (UPDATE only). Logs when it did something."""
    t0, now = time.perf_counter(), time.time()

    def _work() -> dict:
        p = graph.pairs_slice(brain._db, limit=pairs_limit, hub_fraction=hub_fraction)
        f = graph.fade_slice(brain._db, now, limit=fade_limit, half_life_days=half_life_days)
        return {"pair_episodes": p["episodes"], "pairs": p["pairs"], "faded": f}
    async with brain.lock:
        out = await asyncio.to_thread(_work)
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if out["pair_episodes"] or out["faded"]:
        log.info("brain: graph maintain pair_episodes=%d pairs=%d faded=%d ms=%.1f", out["pair_episodes"],
                 out["pairs"], out["faded"], out["ms"])
    return out
