"""orchestrator.prefetch — scheduling wrapper around memory.retrieval.

Responsibility: schedule retrieval tasks and persist their results into the
activation layer.  All retrieval logic lives in memory.retrieval.retrieve;
this module only handles the orchestrator-side lifecycle (background save,
activation update on timeout).
"""
from __future__ import annotations

from memory.retrieval import retrieve
from orchestrator.activation_manager import update_activation
from utils.logging.setup import get_logger

log = get_logger(__name__)


async def run_prefetch(
    layers,
    chat_id: str,
    user_message: str,
    state: str,
    activation,
    topic_return_id: str | None = None,
) -> tuple[list[dict], bool, str | None, dict]:
    """Delegate L3 retrieval to memory.retrieval.retrieve."""
    return await retrieve(layers, chat_id, user_message, state, activation, topic_return_id)


async def save_prefetch_background(
    prefetch_task,
    layers,
    chat_id: str,
    intent: str,
    query_emb,
    turn_state: str,
    activation,
    topic_return_id: str | None,
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
