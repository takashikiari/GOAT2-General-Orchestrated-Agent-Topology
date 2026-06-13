"""Pre-classify orchestration — DAG awareness scan only.

Scans working memory for active DAG sessions and returns their status.
No classification and no override detection are performed here.
The classifier is GOAT's internal tool, called from GoatSupervisor.run(),
not from this module.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.classification.pre_classify")

if TYPE_CHECKING:
    from supervisor.session.history import ConversationHistory

__all__ = ["prepare_classification_context"]


async def prepare_classification_context(
    registry,
    history: "ConversationHistory | None" = None,
    intent: str = "",
    session_id: str = "",
) -> list[dict]:
    """Scan working memory for active DAG sessions.

    Returns the list of active DAG sessions found in working memory.
    No classification or override detection is performed.

    Args:
        registry:   The ServiceRegistry.
        history:    ConversationHistory (unused, kept for API compat).
        intent:     Raw user message (unused, kept for API compat).
        session_id: Current GOAT session ID (unused, kept for API compat).

    Returns:
        Active DAG sessions list — informational only.
    """
    from supervisor.pipeline.dag_awareness import scan_active_dags
    active = await scan_active_dags(registry)
    if active:
        log.info(
            "DAG awareness: %d active session(s) in working memory",
            len(active),
        )
    log.debug(
        "prepare_classification_context: history_turns=%d",
        len(history.messages) if history else 0,
    )
    return active
