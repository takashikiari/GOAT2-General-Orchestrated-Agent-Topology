"""scripts.debug_prefetch — inspect the L3 prefetch daemon's raw output for one query.

Standalone, read-only diagnostic. Reproduces the exact cold-path retrieval
pipeline the prefetch daemon runs (``memory.retrieval._cold``): semantic
search (global + chat-scoped), BM25, GLiNER-routed temporal search, RRF
fusion, entity boost, cross-encoder rerank — against the REAL ChromaDB/Redis
backing store via the real ``ServiceRegistry``. No LLM call is made.

``merge_results`` (RRF) takes labeled ``(mechanism, results)`` groups and
tags each deduped result with every mechanism that returned it — this script
just passes the labels through and prints them in the final table.

Note: the "specific_key" prefetch mechanism referenced in older observability
counters was removed from the pipeline (see memory/layers.py:209) — it is
not reproduced here because it no longer runs in production. The mechanisms
below (semantic_global, semantic_chat_scoped, bm25, temporal) are the ones
actually active in memory/retrieval.py::_cold today.

Usage:
    python3 scripts/debug_prefetch.py "query text"
    python3 scripts/debug_prefetch.py "query text" --chat-id some-chat --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.config import PREFETCH_MAX_RESULTS, PREFETCH_RECENCY_WINDOW_DAYS
from memory.result_merger import merge_results
from memory.temporal_route import parse_interval
from registry.registry import ServiceRegistry


async def _labeled_semantic(layers, chat_id: str, query: str, limit: int, chat_id_filter: str | None):
    results, cache_hit, cache_key = await layers.search_episodic_with_cache(
        chat_id, query, limit=limit, chat_id_filter=chat_id_filter,
    )
    return results, cache_hit, cache_key


async def _labeled_temporal(layers, query: str, limit: int):
    interval = parse_interval(query)
    if not interval:
        return [], None
    after, before = interval
    try:
        results = await layers.search_episodic(query, limit=limit, after=after, before=before)
        return results, (after, before)
    except Exception as exc:  # noqa: BLE001
        print(f"  [temporal search failed: {exc}]")
        return [], (after, before)


def _fmt_recency(metadata: dict, now: float) -> float:
    ts = float((metadata or {}).get("timestamp", 0) or 0)
    window = PREFETCH_RECENCY_WINDOW_DAYS * 86400
    if window <= 0:
        return 0.0
    return max(0.0, 1.0 - (now - ts) / window)


async def run(query: str, chat_id: str, limit: int) -> None:
    registry = ServiceRegistry()
    layers = registry.memory_layers

    print(f"query      = {query!r}")
    print(f"chat_id    = {chat_id!r}  (semantic_chat_scoped will be empty for a fresh id)")
    print(f"limit      = {limit}")
    print()

    query_emb = await layers.embed_query(query)
    print(f"query embedded: {'ok' if query_emb is not None else 'FAILED (would force cold turn)'}")

    entities_dict = await layers.extract_query_entities(query)
    print(f"GLiNER entities: {entities_dict.get('entities', [])} "
          f"types={entities_dict.get('entity_types', [])}")
    print()

    print("--- running mechanisms (mirrors memory/retrieval.py::_cold) ---")
    sem_global, sem_scoped, bm25, (temporal, interval) = await asyncio.gather(
        _labeled_semantic(layers, chat_id, query, limit, None),
        _labeled_semantic(layers, chat_id, query, limit, chat_id),
        layers.bm25_search(query, limit=limit),
        _labeled_temporal(layers, query, limit),
    )
    sem_global_results, sem_global_hit, sem_global_key = sem_global
    sem_scoped_results, sem_scoped_hit, sem_scoped_key = sem_scoped

    print(f"  semantic_global:       {len(sem_global_results)} hits "
          f"(cache_hit={sem_global_hit}, key={sem_global_key})")
    print(f"  semantic_chat_scoped:  {len(sem_scoped_results)} hits "
          f"(cache_hit={sem_scoped_hit}, key={sem_scoped_key})")
    print(f"  bm25:                  {len(bm25)} hits")
    if interval:
        print(f"  temporal:              {len(temporal)} hits (interval={interval})")
    else:
        print("  temporal:              skipped (no DATE/TIME entity detected)")
    print()

    labeled_groups: list[tuple[str, list[dict]]] = [
        ("semantic_global", sem_global_results),
        ("semantic_chat_scoped", sem_scoped_results),
    ]
    if bm25:
        labeled_groups.append(("bm25", bm25))
    if temporal:
        labeled_groups.append(("temporal", temporal))

    merged = merge_results(labeled_groups)[: limit * 2]
    print(f"RRF fusion: {len(merged)} deduped candidates "
          f"(from {sum(len(g) for _, g in labeled_groups)} raw hits across "
          f"{len(labeled_groups)} mechanisms)")

    merged = await layers.boost_by_entities(query, merged, pre_extracted=entities_dict)
    final = (await layers.rerank(query, merged))[:limit]
    print(f"Final (post entity-boost + rerank, capped to {limit}): {len(final)} results")
    print()

    now = time.time()
    print("=" * 100)
    for rank, r in enumerate(final, start=1):
        meta = r.get("metadata", {}) or {}
        mechanisms = r.get("mechanisms") or ["unknown"]
        content = (r.get("content") or "").replace("\n", " ")[:200]
        blended = r.get("blended_score")
        similarity = r.get("score")
        recency = _fmt_recency(meta, now)
        access_count = meta.get("access_count", 0)
        tags = meta.get("tags", "")
        topic_id = meta.get("topic_id", "")

        print(f"#{rank}  blended_score={blended!r}  similarity(raw score)={similarity!r}  "
              f"recency={recency:.3f}  access_count={access_count}")
        print(f"    mechanisms: {', '.join(mechanisms)}")
        print(f"    tags={tags!r}  topic_id={topic_id!r}")
        print(f"    content: {content}")
        print("-" * 100)

    if not final:
        print("(no results — nothing would be written into activation for the next turn)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help="query text to run through the prefetch retrieval pipeline")
    parser.add_argument("--chat-id", default="debug-prefetch-probe",
                         help="chat_id for the chat-scoped semantic search group (default: debug-prefetch-probe)")
    parser.add_argument("--limit", type=int, default=PREFETCH_MAX_RESULTS,
                         help=f"result cap per mechanism (default: PREFETCH_MAX_RESULTS={PREFETCH_MAX_RESULTS})")
    args = parser.parse_args()
    asyncio.run(run(args.query, args.chat_id, args.limit))


if __name__ == "__main__":
    main()
