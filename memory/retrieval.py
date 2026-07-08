"""memory.retrieval — canonical L3 retrieval pipeline (search → merge → boost → rerank).

Single responsibility: given a query and turn state, run the appropriate search
mechanisms, merge results, apply entity boosting and cross-encoder reranking,
and return the ranked list.  Both the prefetch daemon and any on-demand caller
(search_memory tool) use this function so the pipeline is never duplicated.

Query routing:
  GLiNER extracts general entities from the query in parallel with MiniLM and
  BM25, used for entity-overlap boosting (boost_by_entities). Temporal routing
  is independent: temporal_route.parse_interval scans the raw query text
  directly (dateparser, not GLiNER — GLiNER never tags relative expressions
  like "ieri"/"acum 2 ore" as DATE/TIME). If a date/time expression is found,
  it converts to a Unix timestamp interval and a third
  search_episodic(after=, before=) call fires. CrossEncoder then reranks the
  combined candidate pool — it sees the right candidates and can resolve
  "4 iulie 07:00" ↔ "2026-07-04 07:23" correctly.
"""
from __future__ import annotations

import asyncio
import time

from memory.activation import rescore_recency
from memory.budget import enforce_result_limit
from memory.config import PREFETCH_MAX_RESULTS
from memory.result_merger import merge_results
from memory.temporal_route import parse_interval
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
    """L3 retrieval pipeline; state selects warm / drift / cold mechanisms."""
    if state == "warm":
        merged = (
            rescore_recency(activation.merged, time.time())
            if activation and activation.merged else []
        )
        log.info("retrieve warm chat=%s served=%d", chat_id, len(merged))
        return merged, False, None, {"warm_served": True, "thematic": len(merged), "specific_key": 0}

    topic_id = activation.topic_id if activation else None
    if state == "drift":
        return await _drift(layers, chat_id, query, topic_id, activation)
    return await _cold(layers, chat_id, query, topic_id, topic_return_id)


async def _drift(layers, chat_id, query, topic_id, activation=None):
    s, g, b, ents = await asyncio.gather(
        layers.search_episodic(query, limit=_LIMIT, topic_id=topic_id),
        layers.search_episodic(query, limit=_LIMIT),
        layers.bm25_search(query, limit=_LIMIT),
        layers.extract_query_entities(query),
        return_exceptions=True,
    )
    entities_dict = ents if not isinstance(ents, BaseException) else {}
    labeled = [("semantic_topic_scoped", s), ("semantic_global", g)]
    groups = [(name, enforce_result_limit(p)) for name, p in labeled if not isinstance(p, BaseException)]
    bm25 = [] if isinstance(b, BaseException) else b
    temporal = await _temporal_candidates(layers, query)
    # Prediction: previous turn's pre-fetched context added as a candidate group.
    # CrossEncoder scores it against the new query — stays if still relevant,
    # gets ranked out if the topic has shifted.
    prediction = rescore_recency(activation.merged, time.time()) if activation and activation.merged else []
    all_groups = (
        ([("prediction", prediction)] if prediction else [])
        + groups
        + ([("bm25", bm25)] if bm25 else [])
        + ([("temporal", temporal)] if temporal else [])
    )
    merged = merge_results(all_groups)[:_LIMIT * 2]
    merged = await layers.boost_by_entities(query, merged, pre_extracted=entities_dict)
    merged = (await layers.rerank(query, merged))[:_LIMIT]
    log.info(
        "retrieve drift chat=%s merged=%d temporal=%d prediction=%d",
        chat_id, len(merged), len(temporal), len(prediction),
    )
    return merged, False, None, {"warm_served": False, "thematic": len(merged), "specific_key": 0}


async def _cold(layers, chat_id, query, topic_id, topic_return_id):
    async def _cached(filt=None):
        r, h, k = await layers.search_episodic_with_cache(
            chat_id, query, limit=_LIMIT, chat_id_filter=filt,
        )
        return r, h, k

    labels = ["semantic_global", "semantic_chat_scoped"]
    coros = [_cached(), _cached(filt=chat_id)]
    if topic_return_id:
        labels.append("semantic_topic_return")
        coros.append(_topic_search(layers, query, topic_return_id))
    all_res = await asyncio.gather(
        *coros,
        layers.bm25_search(query, limit=_LIMIT),
        layers.extract_query_entities(query),
        return_exceptions=True,
    )
    # last two: bm25 result, entity dict
    entities_dict = all_res[-1] if not isinstance(all_res[-1], BaseException) else {}
    bm25 = [] if isinstance(all_res[-2], BaseException) else all_res[-2]
    groups: list[tuple[str, list[dict]]] = []
    cache_hit, cache_key = False, None
    for label, part in zip(labels, all_res[:-2]):
        if isinstance(part, BaseException):
            log.warning("retrieve cold mechanism raised chat=%s: %s", chat_id, part)
            continue
        r, h, k = part
        groups.append((label, r))
        if k is not None:
            cache_hit, cache_key = h, k
    temporal = await _temporal_candidates(layers, query)
    all_groups = groups + ([("bm25", bm25)] if bm25 else []) + ([("temporal", temporal)] if temporal else [])
    merged = merge_results(all_groups)[:_LIMIT * 2]
    merged = await layers.boost_by_entities(query, merged, pre_extracted=entities_dict)
    merged = (await layers.rerank(query, merged))[:_LIMIT]
    log.info(
        "retrieve cold chat=%s merged=%d bm25=%d temporal=%d",
        chat_id, len(merged), len(bm25), len(temporal),
    )
    ids = [
        r.get("metadata", {}).get("message_id")
        for r in merged if r.get("metadata", {}).get("message_id")
    ]
    if ids:
        asyncio.create_task(layers.bump_access(chat_id, ids))
    return merged, cache_hit, cache_key, {"warm_served": False, "thematic": len(merged), "specific_key": 0}


async def _temporal_candidates(layers, query) -> list[dict]:
    """Timestamp-filtered search when ``query`` contains a date/time expression.

    Parses the raw query text directly (dateparser), independent of GLiNER's
    entity extraction — GLiNER never tags relative expressions ("ieri", "acum
    2 ore") as DATE/TIME, so gating this on GLiNER entities silently skipped
    every relative-time query (confirmed on real data, 2026-07-08).
    """
    interval = parse_interval(query)
    if not interval:
        return []
    after, before = interval
    log.info("temporal route activated after=%.0f before=%.0f", after, before)
    try:
        return await layers.search_episodic(query, limit=_LIMIT, after=after, before=before)
    except Exception as exc:  # noqa: BLE001
        log.warning("temporal search failed: %s", exc)
        return []


async def _topic_search(layers, query: str, topic_return_id: str):
    r = await layers.search_episodic(query, limit=_LIMIT, topic_id=topic_return_id)
    return r, False, None
