"""orchestrator.prefetch — post-turn L3 prefetch daemon.

Runs AFTER the LLM response is delivered, in the inter-turn gap.
No timeout — has as long as it needs before the user sends the next message.
Writes the pre-computed L3 results into activation (L2.5) so the next turn
reads them instantly without touching ChromaDB/BM25/GLiNER/CrossEncoder.
"""
from __future__ import annotations

from memory.retrieval import retrieve
from orchestrator.activation_manager import update_activation
from utils.logging.setup import get_logger

log = get_logger(__name__)


async def run_prefetch_and_save(
    layers,
    chat_id: str,
    intent: str,
    query_emb,
    turn_state: str,
    activation,
    topic_return_id: str | None = None,
    forced_topic_id: str | None = None,
    turn_start: float | None = None,
    precomputed_l3: list[dict] | None = None,
) -> None:
    """Pre-compute L3 for the next turn and persist into activation (L2.5).

    ``turn_start`` (the originating turn's start time, not this daemon's own
    completion time) is forwarded to ``update_activation`` so the write-race
    guard in ``ActivationStore.set`` can tell an out-of-order-finishing but
    logically-older write apart from a genuinely newer one. See
    ``update_activation`` for the full rationale.

    ``precomputed_l3``: on cold/drift turns, ``orchestrator.run()`` already
    calls ``retrieve()`` synchronously for THIS turn's query (to serve the
    current LLM call with fresh, current-query-relevant context — see
    ``orchestrator.py`` step 3). Passing that result through here avoids
    running the exact same search a second time; this daemon just persists it.
    ``None`` (the warm case) preserves the original behaviour: a fresh
    ``"cold"``-mechanism search run in the background, since a warm turn never
    triggers a synchronous search of its own.
    """
    try:
        if precomputed_l3 is not None:
            l3_results = precomputed_l3
            search_state = turn_state
        else:
            search_state = "drift" if turn_state == "warm" else "cold"
            l3_results, _, _, _ = await retrieve(
                layers, chat_id, intent, search_state, activation, topic_return_id,
            )
        await update_activation(
            layers, chat_id, intent, query_emb,
            turn_state, activation, l3_results,
            topic_return_id=topic_return_id, forced_topic_id=forced_topic_id,
            turn_start=turn_start,
        )
        log.info(
            "prefetch ok chat=%s state=%s hits=%d",
            chat_id, search_state, len(l3_results),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("prefetch failed chat=%s: %s", chat_id, exc)
