"""
memory.aits — Adaptive Intent Token Scaling (AITS).

A dynamic per-intent token budget for the orchestrator's memory context,
replacing the fixed cap that dropped the whole L2 conversation block on long
chats. Two real signals only — ``confidence`` (does this query need deep /
recall context?) and ``complexity`` (how much context will the answer need?).
No tool / agent / multi-intent terms: tools (hot-reload plugins) and agents
(DAG pipeline) are separate systems that *consume* memory, not inputs to its
budget. So the budget is purely about how much memory context the kernel
assembles per turn.

All numeric knobs (base, multipliers, bonus, hard cap) come from
``config/memory.toml`` via ``memory.config``. The word lists and the reference
length below are linguistic *content* (like ``_BASE_IDENTITY``), not tunables,
so they stay as documented module constants. No regex (rule 5) — tokenisation
uses ``str.split`` + ``str.strip(string.punctuation)``.
"""
from __future__ import annotations

import logging
import string

from memory.config import (
    BUDGET_BASE,
    BUDGET_COMPLEXITY_MAX_BONUS,
    BUDGET_CONFIDENCE_MULTIPLIER,
    BUDGET_HARD_CAP,
)

log = logging.getLogger(__name__)

__all__ = [
    "calculate_confidence_from_query",
    "calculate_complexity_from_query",
    "calculate_intent_budget",
]

# Linguistic content — not tunable config. Question / explain cues raise
# confidence that the query needs deep context (EN + RO).
_HIGH_CONFIDENCE_WORDS = frozenset({
    "what", "how", "why", "when", "where", "explain", "describe", "detail",
    "details", "elaborate", "summarize", "summarise", "analyze", "analyse",
    "cum", "dece", "când", "unde", "explică", "explica", "detaliază",
    "detalii", "rezumă", "rezuma", "analizează",
})
_MEDIUM_CONFIDENCE_WORDS = frozenset({
    "who", "which", "is", "are", "was", "were", "can", "could", "should",
    "would", "do", "does", "care", "este", "sunt", "erau", "poate", "ar",
    "trebuie", "face", "fac",
})
_LOW_CONFIDENCE_WORDS = frozenset({
    "hi", "hello", "hey", "thanks", "thank", "thx", "ok", "okay", "yes",
    "no", "bye", "salut", "buna", "bună", "salutare", "mulțumesc",
    "mersi", "multumesc", "pa", "la", "revedere",
})

# Reference query length that counts as "full" complexity (≈ a long sentence).
_COMPLEXITY_REF_LENGTH = 200
# Multi-word connectors that signal a compound / multi-entity query.
_COMPLEXITY_CONNECTORS = (" and ", " or ", "și", "sau", " plus ", ";", ",")


def _tokens(query: str) -> set[str]:
    """Lowercase word tokens of ``query``, stripped of surrounding punctuation."""
    return {t for t in (w.strip(string.punctuation) for w in query.lower().split()) if t}


def calculate_confidence_from_query(query: str) -> float:
    """Estimate (0-1) how much deep / recall context this query needs.

    Set-membership over the query tokens against high / medium / low word
    lists. High cues → 0.8 scaled up toward 1.0 by cue count; medium → 0.5;
    low cues → 0.2; a statement with no cue defaults to medium (0.5) — most
    turns benefit from at least recent context.
    """
    words = _tokens(query)
    if not words:
        return 0.2
    high = len(words & _HIGH_CONFIDENCE_WORDS)
    if high:
        return min(0.8 + 0.1 * (high - 1), 1.0)
    if words & _MEDIUM_CONFIDENCE_WORDS:
        return 0.5
    if words & _LOW_CONFIDENCE_WORDS:
        return 0.2
    return 0.5


def calculate_complexity_from_query(query: str) -> float:
    """Estimate (0-1) the query's complexity.

    A length factor (``len / ref``, capped at 1.0) weighted at 0.7 plus a
    connector bonus (multi-word / compound cues) weighted at 0.3, capped at
    1.0. Longer, compound queries are treated as needing more context.
    """
    length_factor = min(len(query) / _COMPLEXITY_REF_LENGTH, 1.0)
    lower = query.lower()
    connector_bonus = 1.0 if any(c in lower for c in _COMPLEXITY_CONNECTORS) else 0.0
    return min(length_factor * 0.7 + connector_bonus * 0.3, 1.0)


def calculate_intent_budget(confidence: float, complexity: float) -> int:
    """Dynamic per-intent token budget for memory context, capped at ``BUDGET_HARD_CAP``.

    Formula: ``base + confidence·multiplier + complexity·complexity_bonus``.
    The result is the budget the orchestrator passes to
    ``MemoryLayers.assemble_context``; L2 is protected independently of it
    (only capped by ``L2_CONTEXT_CAP``), and L3 is gated by the budget that
    remains after L0+L1+L2.
    """
    raw = (
        BUDGET_BASE
        + confidence * BUDGET_CONFIDENCE_MULTIPLIER
        + complexity * BUDGET_COMPLEXITY_MAX_BONUS
    )
    budget = min(int(raw), BUDGET_HARD_CAP)
    log.debug(
        "AITS budget=%d (confidence=%.2f complexity=%.2f base=%d)",
        budget, confidence, complexity, BUDGET_BASE,
    )
    return budget