"""orchestrator.prefetch — decoupled L3 prefetch daemon (warm / drift / cold)."""
import asyncio
import time

from memory.activation import rescore_recency
from memory.budget import enforce_result_limit
from memory.config import PREFETCH_MAX_RESULTS
from memory.result_merger import merge_results
from orchestrator.activation_manager import update_activation
from utils.logging.setup import get_logger

log = get_logger(__name__)
_LIMIT = PREFETCH_MAX_RESULTS


async def run_prefetch(
    layers, chat_id: str, user_message: str, state: str,
    activation, topic_return_id: str | None = None,
) -> tuple[list[dict], bool, str | None, dict]:
    if state == "warm":
        merged = rescore_recency(activation.merged, time.time()) if activation and activation.merged else []
        log.info("prefetch warm chat=%s served=%d", chat_id, len(merged))
        return merged, False, None, {"warm_served": True, "thematic": len(merged), "specific_key": 0}

    topic_id = activation.topic_id if activation else None

    if state == "drift":
        s, g, b = await asyncio.gather(
            layers.search_episodic(user_message, limit=_LIMIT, topic_id=topic_id),
            layers.search_episodic(user_message, limit=_LIMIT),
            layers.bm25_search(user_message, limit=_LIMIT),
            return_exceptions=True,
        )
        groups = [enforce_result_limit(p) for p in (s, g) if not isinstance(p, BaseException)]
        bm25 = [] if isinstance(b, BaseException) else b
        merged = merge_results(groups + ([bm25] if bm25 else []))[:_LIMIT * 2]
        merged = await layers.boost_by_entities(user_message, merged)
        merged = (await layers.rerank(user_message, merged))[:_LIMIT]
        log.info("prefetch drift chat=%s merged=%d", chat_id, len(merged))
        return merged, False, None, {"warm_served": False, "thematic": len(merged), "specific_key": 0}

    return await _cold(layers, chat_id, user_message, topic_id, topic_return_id)

async def _cold(layers, chat_id, user_message, topic_id, topic_return_id):
    async def _cached(filt=None):
        r, h, k = await layers.search_episodic_with_cache(
            chat_id, user_message, limit=_LIMIT, chat_id_filter=filt,
        )
        return r, h, k
    coros = [_cached(), _cached(filt=chat_id)]
    if topic_return_id:
        coros.append(_topic(layers, user_message, topic_return_id))
    all_res = await asyncio.gather(*coros, layers.bm25_search(user_message, limit=_LIMIT), return_exceptions=True)
    bm25 = [] if isinstance(all_res[-1], BaseException) else all_res[-1]
    groups, cache_hit, cache_key = [], False, None
    for part in all_res[:-1]:
        if isinstance(part, BaseException):
            log.warning("prefetch cold mechanism raised chat=%s: %s", chat_id, part)
            continue
        r, h, k = part
        groups.append(r)
        if k is not None:
            cache_hit, cache_key = h, k
    merged = merge_results(groups + ([bm25] if bm25 else []))[:_LIMIT * 2]
    merged = await layers.boost_by_entities(user_message, merged)
    merged = (await layers.rerank(user_message, merged))[:_LIMIT]
    log.info("prefetch cold chat=%s merged=%d bm25=%d", chat_id, len(merged), len(bm25))
    ids = [r.get("metadata", {}).get("message_id") for r in merged if r.get("metadata", {}).get("message_id")]
    if ids:
        asyncio.create_task(layers.bump_access(chat_id, ids))
    return merged, cache_hit, cache_key, {"warm_served": False, "thematic": len(merged), "specific_key": 0}


async def _topic(layers, user_message: str, topic_return_id: str):
    r = await layers.search_episodic(user_message, limit=_LIMIT, topic_id=topic_return_id)
    return r, False, None

async def save_prefetch_background(
    prefetch_task, layers, chat_id: str, intent: str, query_emb,
    turn_state: str, activation, topic_return_id: str | None,
) -> None:
    try:
        l3_results, _, _, _ = await prefetch_task
        await update_activation(
            layers, chat_id, intent, query_emb,
            turn_state, activation, l3_results, topic_return_id=topic_return_id,
        )
        log.info("prefetch background save ok chat=%s hits=%d", chat_id, len(l3_results))
    except Exception as exc:  # noqa: BLE001
        log.warning("prefetch background save failed chat=%s: %s", chat_id, exc)
