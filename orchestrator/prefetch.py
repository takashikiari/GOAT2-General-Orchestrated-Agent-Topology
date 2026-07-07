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
) -> None:
    """Pre-compute L3 for the next turn and persist into activation (L2.5)."""
    try:
        search_state = "drift" if turn_state == "warm" else "cold"
        l3_results, _, _, _ = await retrieve(
            layers, chat_id, intent, search_state, activation, topic_return_id,
        )
        await update_activation(
            layers, chat_id, intent, query_emb,
            turn_state, activation, l3_results,
            topic_return_id=topic_return_id, forced_topic_id=forced_topic_id,
        )
        log.info(
            "prefetch ok chat=%s state=%s hits=%d",
            chat_id, search_state, len(l3_results),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("prefetch failed chat=%s: %s", chat_id, exc)
