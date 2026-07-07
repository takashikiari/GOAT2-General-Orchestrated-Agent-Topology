"""memory.retrieval — canonical L3 retrieval pipeline (search → merge → boost → rerank).

Single responsibility: given a query and turn state, run the appropriate search
mechanisms, merge results, apply entity boosting and cross-encoder reranking,
and return the ranked list.  Both the prefetch daemon (pre-warms next turn) and
any on-demand caller (search_memory tool, etc.) use this function so the pipeline
is never duplicated.
"""
from __future__ import annotations

import asyncio
import time

from memory.activation import rescore_recency
from memory.budget import enforce_result_limit
from memory.config import PREFETCH_MAX_RESULTS
from memory.result_merger import merge_results
from utils.logging.setup import get_logger

log = get_logger(__name__)
_LIMIT = PREFETCH_MAX_RESULTS


async def retrieve(
    layers,
    chat_id: str,
    query: str,
    state: str,
    activation,
    topic_return_id: str | None = None,
) -> tuple[list[dict], bool, str | None, dict]:
    """L3 retrieval pipeline; state selects warm / drift / cold mechanisms.

    Returns (results, cache_hit, cache_key, meta) — same contract as the old
    run_prefetch so callers need no changes.
    """
    if state == "warm":
        merged = (
            rescore_recency(activation.merged, time.time())
            if activation and activation.merged
            else []
        )
        log.info("retrieve warm chat=%s served=%d", chat_id, len(merged))
        return merged, False, None, {"warm_served": True, "thematic": len(merged), "specific_key": 0}

    topic_id = activation.topic_id if activation else None

    if state == "drift":
        s, g, b = await asyncio.gather(
            layers.search_episodic(query, limit=_LIMIT, topic_id=topic_id),
            layers.search_episodic(query, limit=_LIMIT),
            layers.bm25_search(query, limit=_LIMIT),
            return_exceptions=True,
        )
        groups = [enforce_result_limit(p) for p in (s, g) if not isinstance(p, BaseException)]
        bm25 = [] if isinstance(b, BaseException) else b
        merged = merge_results(groups + ([bm25] if bm25 else []))[:_LIMIT * 2]
        merged = await layers.boost_by_entities(query, merged)
        merged = (await layers.rerank(query, merged))[:_LIMIT]
        log.info("retrieve drift chat=%s merged=%d", chat_id, len(merged))
        return merged, False, None, {"warm_served": False, "thematic": len(merged), "specific_key": 0}

    return await _cold(layers, chat_id, query, topic_id, topic_return_id)


async def _cold(
    layers,
    chat_id: str,
    query: str,
    topic_id: str | None,
    topic_return_id: str | None,
) -> tuple[list[dict], bool, str | None, dict]:
    async def _cached(filt=None):
        r, h, k = await layers.search_episodic_with_cache(
            chat_id, query, limit=_LIMIT, chat_id_filter=filt,
        )
        return r, h, k

    coros = [_cached(), _cached(filt=chat_id)]
    if topic_return_id:
        coros.append(_topic_search(layers, query, topic_return_id))
    all_res = await asyncio.gather(
        *coros, layers.bm25_search(query, limit=_LIMIT), return_exceptions=True,
    )
    bm25 = [] if isinstance(all_res[-1], BaseException) else all_res[-1]
    groups: list[list[dict]] = []
    cache_hit, cache_key = False, None
    for part in all_res[:-1]:
        if isinstance(part, BaseException):
            log.warning("retrieve cold mechanism raised chat=%s: %s", chat_id, part)
            continue
        r, h, k = part
        groups.append(r)
        if k is not None:
            cache_hit, cache_key = h, k
    merged = merge_results(groups + ([bm25] if bm25 else []))[:_LIMIT * 2]
    merged = await layers.boost_by_entities(query, merged)
    merged = (await layers.rerank(query, merged))[:_LIMIT]
    log.info("retrieve cold chat=%s merged=%d bm25=%d", chat_id, len(merged), len(bm25))
    ids = [
        r.get("metadata", {}).get("message_id")
        for r in merged
        if r.get("metadata", {}).get("message_id")
    ]
    if ids:
        asyncio.create_task(layers.bump_access(chat_id, ids))
    return merged, cache_hit, cache_key, {"warm_served": False, "thematic": len(merged), "specific_key": 0}


async def _topic_search(layers, query: str, topic_return_id: str):
    r = await layers.search_episodic(query, limit=_LIMIT, topic_id=topic_return_id)
    return r, False, None
