"""MemoryRouter — assembles all router sub-modules into a single search entry-point."""
from __future__ import annotations

from typing import TYPE_CHECKING

from memory.router.cache import RouteCache, make_route_key
from memory.router.classifier import classify_query
from memory.router.confidence import compute_confidence
from memory.router.decision import make_decision
from memory.router.executor import execute_route
from memory.router.layer_stats import LayerStats, LayerStatsTracker
from memory.router.preferences import preferred_layers
from memory.router.types import LayerName, RoutingDecision
from memory.types import AgentRole, MemoryEntry, MemoryLayer

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["MemoryRouter"]


class MemoryRouter:
    """
    Intelligent memory router — drop-in replacement for MemoryManager.search / recall.

    Classifies query intent, routes to the optimal layer(s), and adapts routing
    preferences over time based on observed response latency and hit rates.
    Confidence ≥0.70 → single layer; ≥0.40 → top-2 sequential; <0.40 → full fan-out.
    Identical query patterns are served from the routing cache without re-classification.
    """

    def __init__(self, manager: MemoryManager) -> None:
        self._tracker = LayerStatsTracker()
        self._cache   = RouteCache()
        self._layers: dict[LayerName, MemoryLayer] = {
            "working":   manager.working,
            "episodic":  manager.episodic,
            "long_term": manager.long_term,
        }

    async def search(
        self,
        role: AgentRole,
        query: str,
        *,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Route query to optimal layer(s) and return deduplicated, newest-first results."""
        query_type, strength = classify_query(query)
        route_key  = make_route_key(query, query_type)
        decision   = self._cache.get(route_key)
        if decision is None:
            stats      = self._tracker.snapshot()
            preferred  = preferred_layers(query_type, stats)
            pref_hr    = stats[preferred[0]].hit_rate
            confidence = compute_confidence(query_type, strength, pref_hr)
            decision   = make_decision(query_type, confidence, preferred)
            self._cache.put(route_key, decision)
        return await execute_route(
            decision, role, query,
            limit=limit, layers=self._layers, record=self._tracker.record,
        )

    def stats(self) -> dict[LayerName, LayerStats]:
        """Return a copy of accumulated per-layer routing statistics."""
        return self._tracker.snapshot()

    @property
    def cache_size(self) -> int:
        """Number of cached routing decisions."""
        return self._cache.size
