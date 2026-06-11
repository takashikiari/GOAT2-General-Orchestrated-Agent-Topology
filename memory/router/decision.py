"""Convert confidence score + layer preferences into a RoutingDecision — pure, no I/O."""
from __future__ import annotations

import logging

from memory.router.types import (
    CONF_HIGH, CONF_LOW, Confidence, LayerName, QueryType, RoutingDecision,
)

__all__ = ["make_decision"]

log = logging.getLogger("goat2.memory.router")


def make_decision(
    query_type: QueryType,
    confidence: Confidence,
    preferred: tuple[LayerName, ...],
    *,
    cached: bool = False,
) -> RoutingDecision:
    """
    Apply confidence thresholds to select which layers the executor will query.

    ≥ CONF_HIGH (0.70) → first layer only (single optimal layer).
    ≥ CONF_LOW  (0.40) → first two layers (sequential with early exit).
    < CONF_LOW         → all three layers (parallel fan-out).

    `preferred` must contain all three layers sorted best-first (from preferred_layers).
    Pure — no I/O, no mutable global state.
    """
    if confidence >= CONF_HIGH:
        layers: tuple[LayerName, ...] = preferred[:1]
    elif confidence >= CONF_LOW:
        layers = preferred[:2]
    else:
        layers = preferred   # full fan-out — all three
    decision = RoutingDecision(
        layers=layers,
        confidence=confidence,
        query_type=query_type,
        cached=cached,
    )
    log.debug(
        "make_decision: type=%s conf=%.2f → layers=%s (cached=%s)",
        query_type, float(confidence), layers, cached,
    )
    return decision
