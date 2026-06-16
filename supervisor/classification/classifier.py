"""Intent depth classifier — now a pure parser of GOAT's action (NO LLM).

In the single-call architecture the routing judgment is made by the
one GOAT call (``supervisor.pipeline.goat_call.goat_turn``). The
classifier no longer makes its own LLM call, gathers no context,
and applies no keywords/rules. It simply maps the already-made
``GoatTurnResult.action`` (alias ``GoatDecision`` from
``supervisor.pipeline.goat_decision``) to the ``IntentDepth`` enum:

  action=direct   → CONVERSATIONAL
  action=clarify  → CONVERSATIONAL
  action=dag      → COMPLEX

The ``IntentDepth`` enum is unchanged so existing importers keep working.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.classification.classifier")

if TYPE_CHECKING:
    from supervisor.pipeline.goat_call import GoatTurnResult

__all__ = ["IntentDepth", "classify_intent"]


class IntentDepth(str, Enum):
    """Three-tier routing depth used by GoatSupervisor.run()."""

    CONVERSATIONAL = "conversational"  # direct LLM reply with tools available
    ANALYTICAL     = "analytical"      # lightweight DAG, ≤2 tasks
    COMPLEX        = "complex"         # full DAG with planner, researcher, critic


def classify_intent(turn: "GoatTurnResult") -> IntentDepth:
    """Map GOAT's single-call action to an IntentDepth — pure, no LLM.

    Args:
        turn: The GoatTurnResult produced by the single GOAT call.

    Returns:
        IntentDepth.COMPLEX when ``action == "dag"``, otherwise
        IntentDepth.CONVERSATIONAL (both ``direct`` and ``clarify`` are answered
        on the conversational path).
    """
    depth = IntentDepth.COMPLEX if turn.action == "dag" else IntentDepth.CONVERSATIONAL
    log.debug("classify_intent: action=%s → %s", turn.action, depth.value)
    return depth
