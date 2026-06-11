"""Classify a free-text query into a routing category — pure, no I/O, PyO3 candidate."""
from __future__ import annotations

import logging
import re
from typing import Final

from memory.router.types import QueryType

__all__ = ["classify_query"]

log = logging.getLogger("goat2.memory.router")

# Explicit time references: "yesterday", "last week", "3 days ago", "when did", etc.
_TEMPORAL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(yesterday|today|last\s+\w+|this\s+\w+|\d+\s*(?:day|week|month|year)s?\s+ago"
    r"|before|after|since|until|when\s+did|at\s+\d|on\s+\w+day)\b",
    re.IGNORECASE,
)

# Recency intent: "latest", "most recent", "recently", "just", "current(ly)", etc.
_RECENCY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(latest|most\s+recent(?:ly)?|just\s+now|current(?:ly)?|newest|just|recent(?:ly)?)\b",
    re.IGNORECASE,
)

_MIN_SEMANTIC_WORDS: Final[int] = 4   # longer queries with no other signal → semantic


def classify_query(query: str) -> tuple[QueryType, float]:
    """
    Return (QueryType, pattern_strength ∈ [0.0, 1.0]).
    Multiple temporal markers raise strength toward 1.0.
    Single-word or empty queries return ("unknown", 0.0).
    Pure — no I/O, no mutable global state. PyO3 candidate once regex is ported.
    """
    q = query.strip()
    if not q or len(q.split()) < 2:
        log.debug("classify_query: empty/short query → unknown")
        return "unknown", 0.0

    temporal_hits = _TEMPORAL_RE.findall(q)
    if temporal_hits:
        strength = min(1.0, 0.60 + 0.15 * len(temporal_hits))
        log.debug("classify_query: temporal (hits=%d, strength=%.2f)", len(temporal_hits), strength)
        return "temporal", strength

    if _RECENCY_RE.search(q):
        log.debug("classify_query: recency (strength=0.80)")
        return "recency", 0.80

    if len(q.split()) >= _MIN_SEMANTIC_WORDS:
        log.debug("classify_query: semantic (strength=0.60)")
        return "semantic", 0.60

    log.debug("classify_query: generic (strength=0.40)")
    return "generic", 0.40
