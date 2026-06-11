"""Shared types for the intelligent memory router.

All types are Rust-ready with explicit type hints, Final constants,
and frozen dataclasses for immutability.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Literal, NewType

log = logging.getLogger("goat2.memory.router")

# Rust equivalent: enum QueryType
QueryType = Literal["temporal", "semantic", "recency", "generic", "unknown"]

# Rust equivalent: enum LayerName (mirrors MemoryTierLiteral)
LayerName = Literal["working", "episodic", "long_term"]

# Rust equivalents: struct Confidence(f64), struct RouteKey(String), type Millis = f64
Confidence = NewType("Confidence", float)  # normalised score in [0.0, 1.0]
RouteKey = NewType("RouteKey", str)  # hashed query-pattern cache identifier
Millis = NewType("Millis", float)  # wall-clock duration in milliseconds

# Layer iteration order — stable reference shared across all router modules.
_ALL_LAYERS: Final[tuple[LayerName, ...]] = ("working", "episodic", "long_term")

# Routing confidence thresholds — single source of truth.
CONF_HIGH: Final[float] = 0.70  # ≥ this → single layer
CONF_LOW: Final[float] = 0.40  # ≥ this → top-2 layers; below → full fan-out


@dataclass(frozen=True)
class RoutingDecision:
    """
    Immutable outcome of the routing algorithm for one query.

    Rust equivalent: struct with frozen fields.
    """

    layers: tuple[LayerName, ...]  # layers to try, in preference order
    confidence: Confidence  # confidence score [0.0, 1.0]
    query_type: QueryType  # classified query type
    cached: bool  # True when retrieved from RouteCache


@dataclass(frozen=True)
class LayerTiming:
    """
    Completed-query record passed to LayerStatsTracker.record().

    Rust equivalent: struct for timing metrics.
    """

    layer: LayerName  # layer name queried
    duration_ms: Millis  # duration in milliseconds
    hit: bool  # True when layer returned ≥1 result
