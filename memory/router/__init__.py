"""memory.router — intelligent memory router with adaptive layer selection."""
from __future__ import annotations

import logging

from memory.router.router import MemoryRouter
from memory.router.types import (
    CONF_HIGH, CONF_LOW,
    Confidence, LayerName, LayerTiming, Millis,
    QueryType, RoutingDecision, RouteKey,
)
from memory.router.layer_stats import LayerStats, LayerStatsTracker
from memory.router.classifier import classify_query
from memory.router.cache import make_route_key

log = logging.getLogger("goat2.memory.router")

__all__ = [
    "MemoryRouter",
    # types
    "CONF_HIGH", "CONF_LOW",
    "Confidence", "LayerName", "LayerTiming", "Millis",
    "QueryType", "RoutingDecision", "RouteKey",
    # stats
    "LayerStats", "LayerStatsTracker",
    # pure helpers
    "classify_query", "make_route_key",
]
