"""Intent classification — pure-Python routing of a turn's
action. Routes one of:

  - ``direct``  — GOAT answered in chat, no DAG needed.
  - ``clarify`` — GOAT asked a clarifying question.
  - ``dag``     — GOAT spawned a DAG (detected from called_tools).

The classifier is a thin wrapper over a
``supervisor.pipeline.goat_call.GoatTurnResult``. It reads the
``action`` field which the call pipeline already populated.

USAGE:
    from supervisor.classification.classifier import classify_intent

    depth = classify_intent(turn_result)
    if depth is IntentDepth.DAG:
        ...collect results later...

WHY A SEPARATE MODULE:
    The action is decided inside the one LLM call. Other layers
    (the supervisor, the Telegram interface, the audit log) all
    want to know "what kind of turn was that?" without coupling
    to the LLM call. This module is the single source of truth
    for that answer.
"""
from __future__ import annotations

from enum import Enum
from typing import Final

__all__ = ["IntentDepth", "DIRECT", "CLARIFY", "DAG", "classify_intent"]


class IntentDepth(str, Enum):
    """Routing depth for one turn."""

    DIRECT  = "direct"   # chat reply, no DAG
    CLARIFY = "clarify"  # clarifying question
    DAG     = "dag"      # DAG spawned (or in flight)


# Convenient string aliases — callers can use either.
DIRECT:  Final[str] = IntentDepth.DIRECT.value
CLARIFY: Final[str] = IntentDepth.CLARIFY.value
DAG:     Final[str] = IntentDepth.DAG.value


def classify_intent(turn) -> IntentDepth:
    """Map a ``GoatTurnResult`` to its routing depth.

    Args:
        turn: Any object exposing ``.action`` (str) — typically a
            ``GoatTurnResult``. The classifier tolerates missing
            attributes and defaults to DIRECT.

    Returns:
        The ``IntentDepth`` for the turn.
    """
    raw = getattr(turn, "action", None)
    if raw == "dag":
        return IntentDepth.DAG
    if raw == "clarify":
        return IntentDepth.CLARIFY
    return IntentDepth.DIRECT