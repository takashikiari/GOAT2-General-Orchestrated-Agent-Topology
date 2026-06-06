"""Compute routing confidence from classifier output and layer history — pure, PyO3 candidate."""
from __future__ import annotations

from typing import Final

from memory.router.types import Confidence, QueryType

__all__ = ["compute_confidence"]

_WEIGHT_PATTERN: Final[float] = 0.70   # contribution from classifier pattern strength
_WEIGHT_HISTORY: Final[float] = 0.30   # contribution from layer's historical hit rate


def compute_confidence(
    query_type: QueryType,
    pattern_strength: float,
    hit_rate: float,
) -> Confidence:
    """
    confidence = 0.70 × pattern_strength + 0.30 × hit_rate.

    Returns 0.0 for "unknown" type, which forces full fan-out in make_decision.
    Both inputs are expected in [0.0, 1.0]; result is clamped to the same range.
    Pure — no I/O, no mutable global state. PyO3 candidate.
    """
    if query_type == "unknown":
        return Confidence(0.0)
    score = _WEIGHT_PATTERN * pattern_strength + _WEIGHT_HISTORY * hit_rate
    return Confidence(min(1.0, max(0.0, score)))
