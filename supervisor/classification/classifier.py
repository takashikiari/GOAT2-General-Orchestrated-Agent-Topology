"""Intent depth classifier — now a pure parser of GOAT's decision (NO LLM).

In the single-call architecture the routing judgment is made by the one GOAT
decision call (``supervisor.pipeline.goat_decision.decide``). The classifier no
longer makes its own LLM call, gathers no context, and applies no keywords/rules.
It simply maps the already-made ``GoatDecision.action`` to the ``IntentDepth``
enum that the rest of the supervisor understands:

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
    from supervisor.pipeline.goat_decision import GoatDecision

__all__ = ["IntentDepth", "classify_intent"]


class IntentDepth(str, Enum):
    """Three-tier routing depth used by GoatSupervisor.run()."""

    CONVERSATIONAL = "conversational"  # direct LLM reply with tools available
    ANALYTICAL     = "analytical"      # lightweight DAG, ≤2 tasks
    COMPLEX        = "complex"         # full DAG with planner, researcher, critic


def classify_intent(decision: "GoatDecision") -> IntentDepth:
    """Map GOAT's single-call decision to an IntentDepth — pure, no LLM.

    Args:
        decision: The GoatDecision produced by the single GOAT call.

    Returns:
        IntentDepth.COMPLEX when ``action == "dag"``, otherwise
        IntentDepth.CONVERSATIONAL (both ``direct`` and ``clarify`` are answered
        on the conversational path).
    """
    depth = IntentDepth.COMPLEX if decision.action == "dag" else IntentDepth.CONVERSATIONAL
    log.debug("classify_intent: action=%s → %s", decision.action, depth.value)
    return depth
