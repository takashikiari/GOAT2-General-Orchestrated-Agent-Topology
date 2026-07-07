"""orchestrator.activation_manager — per-chat activation persistence after prefetch."""
import time
import uuid

from memory.activation import (
    Activation, archive_current_topic, trim_recent, update_centroid_weighted,
)
from memory.config import TOPIC_ARCHIVE_MAX
from utils.logging.setup import get_logger

log = get_logger(__name__)


async def update_activation(
    layers, chat_id: str, intent: str, query_emb,
    turn_state: str, activation, l3_results: list[dict],
    topic_return_id: str | None = None,
    forced_topic_id: str | None = None,
):
    now = time.time()
    if turn_state == "warm":
        if activation is None:
            return None
        activation.recent_queries = trim_recent(activation.recent_queries, intent)
        activation.turn_count += 1
        activation.ts = now
        await layers.set_activation(chat_id, activation)
        return activation

    if query_emb is None:
        return None

    recent = trim_recent(activation.recent_queries if activation else [], intent)
    archived: list[dict] = []
    if activation:
        if turn_state == "cold" and activation.topic_id:
            archived = archive_current_topic(activation, TOPIC_ARCHIVE_MAX)
        else:
            archived = list(activation.archived_topics)

    if turn_state == "drift" and activation and activation.centroid:
        new_centroid = update_centroid_weighted(
            activation.centroid, query_emb, activation.turn_count + 1,
        )
        new_turn_count = activation.turn_count + 1
        topic_id = forced_topic_id or activation.topic_id or str(uuid.uuid4())
    else:
        new_centroid = query_emb
        new_turn_count = 1
        topic_id = forced_topic_id or topic_return_id or str(uuid.uuid4())
        if topic_return_id and not forced_topic_id:
            log.info("topic return chat=%s topic=%s", chat_id, topic_return_id)

    new_act = Activation(
        centroid=new_centroid,
        merged=l3_results,
        last_query=intent,
        recent_queries=recent,
        ts=now,
        topic_id=topic_id,
        turn_count=new_turn_count,
        archived_topics=archived,
    )
    await layers.set_activation(chat_id, new_act)
    return new_act
