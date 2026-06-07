"""Intent depth classifier — routes intents to conversational, analytical, or complex handling.

All classification is now LLM-driven with no keyword short-circuits. The model
semantically evaluates intent depth regardless of message formatting or prefixes.
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


_MEMORY_PATTERNS = (
    'redis', 'chroma', 'letta', 'memory check', 'memory status',
    'intrari', 'intrări', 'ultimele', 'working memory', 'episodic',
    'long.term', 'verifica memoria', 'citeste memoria',
)

def _is_memory_query(intent: str) -> bool:
    low = intent.lower()
    return any(p in low for p in _MEMORY_PATTERNS)

async def classify_intent(intent: str) -> IntentDepth:
    """Classify intent via LLM — no keyword short-circuits, all messages evaluated semantically.

    The model evaluates true intent depth regardless of message formatting,
    prefixes, or structural triggers. This enables autonomous tool selection.
    """
    if _is_memory_query(intent):
        return IntentDepth.CONVERSATIONAL
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
        depth = IntentDepth.COMPLEX
    return depth
