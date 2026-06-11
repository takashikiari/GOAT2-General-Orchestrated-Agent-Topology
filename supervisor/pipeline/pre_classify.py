"""Pre-classify orchestration — DAG awareness + override persistence.

This module extracts the steps that the supervisor's `run()` method
performs between session init and intent classification:

  1. Scan working memory for active DAG sessions (DAG awareness).
  2. Make the conversation history reachable to the classifier
     (attached to the registry as `_history`).
  3. Persist any explicit user override for the rest of the session.

The classifier LLM uses these signals to:
  - prefer CONVERSATIONAL for follow-ups about in-flight DAGs,
  - apply the override unconditionally when one is present.

The module is intentionally a free function so the supervisor class
stays focused on routing and DAG execution, and the orchestration
logic is testable in isolation.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.pipeline")

if TYPE_CHECKING:
    from supervisor.session.history import ConversationHistory

__all__ = ["prepare_classification_context"]


async def prepare_classification_context(
    registry,
    history: "ConversationHistory",
    intent: str,
    session_id: str,
) -> list[dict]:
    """Prepare the supervisor + registry for LLM-based intent classification.

    Performs three steps in order:
      1. Scan working memory for active DAG sessions (DAG awareness).
      2. Attach the in-memory history to the registry so the
         classifier can read it through `registry._history`.
      3. Detect (semantically) and persist any explicit override
         for this session.

    Args:
        registry: The ServiceRegistry.
        history:  The current ConversationHistory instance.
        intent:   The raw user message text.
        session_id: The current GOAT session ID (UUID string).

    Returns:
        The list of active DAG sessions found in working memory
        (empty if none). The return value is informational only —
        the classifier reads the same list independently.
    """
    from supervisor.pipeline.dag_awareness import (
        scan_active_dags,
        persist_session_override,
    )
    active = await scan_active_dags(registry)
    if active:
        log.info(
            "DAG awareness: %d active session(s) in working memory",
            len(active),
        )
    # Attach history to the registry so the classifier can read it
    # through `registry._history` without a cross-module reference.
    registry._history = history  # type: ignore[attr-defined]
    # Persist any explicit user override (semantic) for the session.
    await persist_session_override(registry, intent, session_id)
    return active
