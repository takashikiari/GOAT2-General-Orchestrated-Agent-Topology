"""Intent depth classifier — routes intents to conversational, analytical, or complex handling.

All classification is LLM-driven with no keyword short-circuits. The model
semantically evaluates intent depth regardless of message formatting or prefixes.
Memory queries are routed through the same semantic path — if a user asks about
memory, the LLM may classify it as analytical or complex if it requires DAG tools.

FALLBACK SAFEGUARD (FIX):
=========================
If the LLM returns empty or unparseable output, we fall back to ANALYTICAL
(a lightweight DAG with ≤2 tasks) instead of COMPLEX (full DAG). This prevents
token waste on unnecessary full DAG execution when the classifier fails.
"""
from __future__ import annotations

from enum import Enum
from typing import Final

from config.settings import settings
from supervisor.llm_utils import _call_llm

__all__ = ["IntentDepth", "classify_intent"]

_CLASSIFIER_SYSTEM: Final[str] = (
    "Classify the user intent into exactly one depth level:\n"
    "  conversational — greetings, chitchat, quick questions, simple definitions\n"
    "  analytical     — explain concepts, compare options, light coding, structured analysis\n"
    "  complex        — multi-step research, full implementation, architecture design\n"
    "Reply with ONLY the single word: conversational, analytical, or complex."
)


class IntentDepth(str, Enum):
    """Three-tier routing depth used by GoatSupervisor.run()."""

    CONVERSATIONAL = "conversational"  # direct LLM reply with tools available
    ANALYTICAL     = "analytical"      # lightweight DAG, ≤2 tasks
    COMPLEX        = "complex"         # full DAG with planner, researcher, critic


async def classify_intent(intent: str) -> IntentDepth:
    """Classify intent via LLM — no keyword short-circuits, all messages evaluated semantically.

    The model evaluates true intent depth regardless of message formatting,
    prefixes, or structural triggers. Memory queries are routed through the
    same semantic path, allowing the LLM to determine if they need DAG tools.

    FALLBACK SAFEGUARD:
    ===================
    If the LLM returns empty or unparseable output, we fall back to ANALYTICAL
    (lightweight DAG) instead of COMPLEX (full DAG). This prevents token waste
    on unnecessary full DAG execution when the classifier fails.
    """
    raw = await _call_llm(
        settings.agents.get("memory"),  # gpt-4o-mini — fast, cheap
        [
            {"role": "system", "content": _CLASSIFIER_SYSTEM},
            {"role": "user",   "content": intent},
        ],
    )
    token = raw.strip().lower().split()[0] if raw.strip() else ""
    try:
        depth = IntentDepth(token)
    except ValueError:
        # FIX: Fall back to ANALYTICAL (lightweight) instead of COMPLEX (full DAG)
        # This prevents token waste when the classifier fails
        depth = IntentDepth.ANALYTICAL
    return depth
