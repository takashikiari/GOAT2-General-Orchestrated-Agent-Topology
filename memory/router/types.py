"""Shared types for the intelligent memory router — all Rust-ready, no dict[str, Any]."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, NewType

# Rust equivalent: enum QueryType
QueryType = Literal["temporal", "semantic", "recency", "generic", "unknown"]

# Rust equivalent: enum LayerName  (mirrors MemoryTierLiteral in memory_enums.py)
LayerName = Literal["working", "episodic", "long_term"]

# Rust equivalents: struct Confidence(f64), struct RouteKey(String), type Millis = f64
Confidence = NewType("Confidence", float)   # normalised score in [0.0, 1.0]
RouteKey   = NewType("RouteKey",   str)     # hashed query-pattern cache identifier
Millis     = NewType("Millis",     float)   # wall-clock duration in milliseconds

# Layer iteration order — stable reference shared across all router modules.
_ALL_LAYERS: Final[tuple[LayerName, ...]] = ("working", "episodic", "long_term")

# Routing confidence thresholds — single source of truth for decision + executor.
CONF_HIGH: Final[float] = 0.70   # ≥ this → single layer
CONF_LOW:  Final[float] = 0.40   # ≥ this → top-2 layers; below → full fan-out


@dataclass(frozen=True)
class RoutingDecision:
    """Immutable outcome of the routing algorithm for one query."""
    layers:     tuple[LayerName, ...]   # straturi de încercat, în ordinea preferinței
    confidence: Confidence              # scorul de încredere [0.0, 1.0]
    query_type: QueryType               # tipul interogării clasificate
    cached:     bool                    # True când a fost preluat din RouteCache


@dataclass(frozen=True)
class LayerTiming:
    """Completed-query record passed to LayerStatsTracker.record()."""
    layer:       LayerName   # numele stratului interogat
    duration_ms: Millis      # durata în milisecunde
    hit:         bool        # True când stratul a returnat ≥1 rezultat
