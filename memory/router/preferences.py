"""Layer affinity table + adaptive reordering by historical hit rate.

Pure functions — PyO3 candidates. Blends static affinity scores with
observed hit rates for intelligent layer preference ordering.
"""
from __future__ import annotations

import logging
from typing import Final

from memory.router.layer_stats import LayerStats
from memory.router.types import LayerName, QueryType, _ALL_LAYERS

__all__ = ["preferred_layers"]

log = logging.getLogger("goat2.memory.router")

# Base affinity scores per (query_type, layer). Higher = more preferred.
# Rust equivalent: static AFFINITY: &[(QueryType, [(LayerName, f64); 3])]
_AFFINITY: Final[dict[QueryType, dict[LayerName, float]]] = {
    "temporal": {"episodic": 0.90, "long_term": 0.70, "working": 0.30},
    "recency": {"working": 0.90, "episodic": 0.70, "long_term": 0.40},
    "semantic": {"episodic": 0.90, "long_term": 0.70, "working": 0.30},
    "generic": {"working": 0.60, "episodic": 0.60, "long_term": 0.60},
    "unknown": {"working": 0.50, "episodic": 0.50, "long_term": 0.50},
}

_W_AFFINITY: Final[float] = 0.70  # weight for static affinity score
_W_HISTORY: Final[float] = 0.30  # weight for observed hit rate


def preferred_layers(
    query_type: QueryType,
    stats: dict[LayerName, LayerStats],
) -> tuple[LayerName, ...]:
    """
    Return all three layers sorted best-first.

    Blends static affinity with observed hit rates. Layers with no call
    history keep their static affinity rank.

    Pure function — given the same inputs always returns the same output.
    PyO3 candidate.
    """
    affinity = _AFFINITY.get(query_type, _AFFINITY["unknown"])

    def _score(layer: LayerName) -> float:
        base = affinity.get(layer, 0.50)
        s = stats.get(layer)
        if s is None or s.calls == 0:
            return base
        return _W_AFFINITY * base + _W_HISTORY * s.hit_rate

    ordered = tuple(sorted(_ALL_LAYERS, key=_score, reverse=True))
    log.debug("preferred_layers: query_type=%s → %s", query_type, ordered)
    return ordered
