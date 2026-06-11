"""MemoryRouter — Intelligent memory routing with adaptive preferences.

Assembles all router sub-modules into a single search entry-point.
Classifies query intent, routes to optimal layer(s), and adapts
routing preferences based on observed latency and hit rates.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.router.cache import RouteCache, make_route_key
from memory.router.classifier import classify_query
from memory.router.confidence import compute_confidence
from memory.router.decision import make_decision
from memory.router.executor import execute_route
from memory.router.layer_stats import LayerStats, LayerStatsTracker
from memory.router.preferences import preferred_layers
from memory.router.types import LayerName, RoutingDecision

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager
    from memory.shared.types import AgentRole, MemoryEntry, MemoryLayer

log = logging.getLogger("goat2.memory.router")

__all__ = ["MemoryRouter"]


class MemoryRouter:
    """
    Intelligent memory router — drop-in replacement for MemoryManager.search.

    Classifies query intent, routes to optimal layer(s), and adapts routing
    preferences over time based on observed response latency and hit rates.

    Confidence thresholds:
    - ≥0.70: Single layer (high confidence)
    - ≥0.40: Top-2 layers sequential (medium confidence)
    - <0.40: Full fan-out (low confidence)

    Identical query patterns are served from routing cache without
    re-classification for performance.
    """

    def __init__(self, manager: MemoryManager) -> None:
        log.debug("MemoryRouter: initialising with manager=%s", type(manager).__name__)
        self._tracker: LayerStatsTracker = LayerStatsTracker()
        self._cache: RouteCache = RouteCache()
        self._layers: dict[LayerName, MemoryLayer] = {
            "working": manager.working,
            "episodic": manager.episodic,
            "long_term": manager.long_term,
        }
        log.info("MemoryRouter: ready (cache_maxsize=%d)", self._cache.size)

    async def search(
        self,
        role: AgentRole,
        query: str,
        *,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """
        Route query to optimal layer(s) and return deduplicated results.

        Results are sorted by recency (newest first). Uses cached routing
        decisions for identical query patterns when available.
        """
        query_type, strength = classify_query(query)
        route_key = make_route_key(query, query_type)
        decision = self._cache.get(route_key)

        if decision is None:
            stats = self._tracker.snapshot()
            preferred = preferred_layers(query_type, stats)
            pref_hr = stats[preferred[0]].hit_rate
            confidence = compute_confidence(query_type, strength, pref_hr)
            decision = make_decision(query_type, confidence, preferred)
            self._cache.put(route_key, decision)
            log.debug(
                "MemoryRouter.search: cache MISS — query_type=%s confidence=%.2f layers=%s",
                query_type, float(confidence), preferred,
            )
        else:
            log.debug("MemoryRouter.search: cache HIT — layers=%s", decision.layers)

        return await execute_route(
            decision,
            role,
            query,
            limit=limit,
            layers=self._layers,
            record=self._tracker.record,
        )

    def stats(self) -> dict[LayerName, LayerStats]:
        """Return a copy of accumulated per-layer routing statistics."""
        return self._tracker.snapshot()

    @property
    def cache_size(self) -> int:
        """Number of cached routing decisions."""
        return self._cache.size
