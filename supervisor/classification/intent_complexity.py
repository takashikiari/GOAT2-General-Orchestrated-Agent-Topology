"""Intent complexity scoring — analyse the raw user intent.

Pure Python, no LLM, no regex, no I/O. Returns a numeric score
in [0.0, 1.0] and a label (``"trivial"`` / ``"simple"`` /
``"complex"``) so callers can branch on intent complexity
without coupling to the LLM-driven ``classify_intent`` action
mapper.

USAGE:
    from supervisor.classification.intent_complexity import (
        score_intent_complexity,
    )

    score = score_intent_complexity("Analizează totul și construiește un plan.")
    if score.label == "complex":
        ... # maybe spawn a DAG

SCORING:
    The score combines four signals, each in [0, 1]:
      - length      : log-scaled character count, plateauing around 1.0
      - tokens      : word count, similarly plateaued
      - clauses     : density of clause markers (',', ';', ' și ', ' then ')
      - verb_match  : fraction of recognised "complex verbs" (analyse,
                      build, compare, etc.) found in the intent
    Final score = weighted sum clamped to [0.0, 1.0]. The labels are
    derived from two thresholds (``COMPLEXITY_THRESHOLDS``).

WHY THIS EXISTS (BUG-017):
    The previous ``classify_intent`` was a trivial mapper from
    ``turn.action`` to an enum. It said nothing about how
    complex the user's intent was. A long multi-clause request
    ("compare X and Y, then build a report") and a one-word
    "ok" were routed identically. The new scorer gives the
    supervisor a signal it can use to skip the memory recall
    step for trivial inputs, or trigger a DAG for complex
    ones — without a second LLM call.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

log = logging.getLogger("goat2.supervisor.classification.intent_complexity")

__all__ = [
    "ComplexityScore",
    "COMPLEXITY_THRESHOLDS",
    "COMPLEX_VERBS",
    "score_intent_complexity",
]


@dataclass(frozen=True)
class ComplexityScore:
    """The result of ``score_intent_complexity``.

    Attributes:
        value: Numeric score in [0.0, 1.0].
        label: One of ``"trivial"``, ``"simple"``, ``"complex"``.
    """

    value: float
    label: str


# Thresholds in [0, 1] separating the three bands:
#   score < THRESHOLDS[0]  -> "trivial"
#   THRESHOLDS[0] <= score < THRESHOLDS[1]  -> "simple"
#   score >= THRESHOLDS[1] -> "complex"
# Tunable via config/goat.toml [intent_complexity] in a follow-up.
COMPLEXITY_THRESHOLDS: Final[tuple[float, float]] = (0.20, 0.60)


# Romanian + English "complex" verbs. Pure substring / token
# containment (no regex). Lowercased.
COMPLEX_VERBS: Final[frozenset[str]] = frozenset({
    # Romanian
    "analizează", "compară", "compară", "construiește", "generează",
    "planifică", "rezolvă", "proiectează", "transformă", "investighează",
    "explică", "descrie", "rezumă", "sintetizează", "organizează",
    # English
    "analyze", "compare", "build", "generate", "plan", "solve",
    "design", "transform", "investigate", "explain", "describe",
    "summarise", "summarize", "synthesise", "synthesize", "organize",
})


# Clause markers we count for the clauses signal. Pure substring
# presence (no regex). Punctuation + connective words.
_CLAUSE_MARKERS: Final[tuple[str, ...]] = (
    ",", ";", " și ", " and ", " then ", " apoi ", " dar ", " but ",
    " while ", " în timp ce ", " because ", " pentru că ",
)


def _length_signal(text: str) -> float:
    """Log-scaled character count, plateauing around 1.0 at ~1000 chars."""
    if not text:
        return 0.0
    n = len(text)
    # log(1 + n) / log(1 + 1000). Saturates near 1.0 around 1000 chars.
    import math
    return min(1.0, math.log1p(n) / math.log1p(1000.0))


def _token_signal(text: str) -> float:
    """Word count, plateauing around 1.0 at ~50 tokens."""
    if not text:
        return 0.0
    tokens = text.split()
    n = len(tokens)
    if n == 0:
        return 0.0
    import math
    return min(1.0, math.log1p(n) / math.log1p(50.0))


def _clause_signal(text: str) -> float:
    """Density of clause markers — a multi-clause intent is more
    complex than a single one. Returns the count of distinct
    marker occurrences, capped at 4."""
    if not text:
        return 0.0
    lc = text.lower()
    count = 0
    for marker in _CLAUSE_MARKERS:
        if marker in lc:
            count += 1
    return min(1.0, count / 4.0)


def _verb_signal(text: str) -> float:
    """Fraction of recognised complex verbs found in the intent."""
    if not text:
        return 0.0
    lc = text.lower()
    tokens = [tok.strip(".,;:!?()[]\"'") for tok in lc.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return 0.0
    matches = sum(1 for tok in tokens if tok in COMPLEX_VERBS)
    return min(1.0, matches / max(1, len(tokens)) * 3.0)


# Weight of each signal in the final score. The four signals
# together cover the main dimensions of "this looks like a
# non-trivial request": the raw text is long, has many words,
# contains multiple clauses, and uses a complex verb.
_WEIGHTS: Final[tuple[float, float, float, float]] = (
    0.30,  # length
    0.20,  # tokens
    0.30,  # clauses
    0.20,  # verbs
)


def score_intent_complexity(text: str) -> ComplexityScore:
    """Compute the complexity score of a raw user intent.

    Args:
        text: Raw user intent (any language). Empty / whitespace
            scores 0.0 and is labelled ``"trivial"``.

    Returns:
        ``ComplexityScore(value, label)`` where ``value`` is in
        ``[0.0, 1.0]`` and ``label`` is one of ``"trivial"``,
        ``"simple"``, ``"complex"`` per ``COMPLEXITY_THRESHOLDS``.
    """
    if not text or not text.strip():
        return ComplexityScore(value=0.0, label="trivial")
    s_len    = _length_signal(text)
    s_tok    = _token_signal(text)
    s_clause = _clause_signal(text)
    s_verb   = _verb_signal(text)
    value = (
        _WEIGHTS[0] * s_len
        + _WEIGHTS[1] * s_tok
        + _WEIGHTS[2] * s_clause
        + _WEIGHTS[3] * s_verb
    )
    # Clamp to [0, 1] just in case weights sum to > 1.
    value = max(0.0, min(1.0, value))
    low, high = COMPLEXITY_THRESHOLDS
    if value < low:
        label = "trivial"
    elif value < high:
        label = "simple"
    else:
        label = "complex"
    return ComplexityScore(value=value, label=label)