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
    turn_start: float | None = None,
):
    """Persist this turn's activation update, guarded against write races.

    ``turn_start`` is the ORIGIN wall-clock of the turn that produced this
    update (``orchestrator.run()``'s ``start``, captured before any I/O) —
    not the time this background prefetch happens to finish. It becomes the
    written ``Activation.ts``, which ``ActivationStore.set`` uses as an
    atomic monotonic ordering key: if turn N's prefetch is slow and finishes
    AFTER turn N+1's already-faster prefetch wrote, turn N's write carries
    the OLDER ``ts`` and is rejected instead of silently clobbering turn
    N+1's fresher activation. Defaults to ``time.time()`` (the old,
    completion-time behaviour) when not supplied, for callers/tests that
    don't need the ordering guarantee.
    """
    now = turn_start if turn_start is not None else time.time()
    if turn_state == "warm":
        if activation is None:
            return None
        activation.recent_queries = trim_recent(activation.recent_queries, intent)
        activation.turn_count += 1
        activation.ts = now
        await layers.set_activation(chat_id, activation)
        log.info(
            "activation updated chat=%s state=warm topic=%s turn=%d",
            chat_id, activation.topic_id, activation.turn_count,
        )
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
    log.info(
        "activation updated chat=%s state=%s topic=%s turn=%d",
        chat_id, turn_state, topic_id, new_turn_count,
    )
    return new_act
