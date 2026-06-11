"""Execute a RoutingDecision against memory layers — records per-layer timing.

Pure execution logic — given a RoutingDecision, queries the specified layers
and records timing statistics for adaptive routing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from memory.router.types import (
    CONF_HIGH,
    CONF_LOW,
    LayerName,
    LayerTiming,
    Millis,
    RoutingDecision,
)

if TYPE_CHECKING:
    from memory.shared.types import AgentRole, MemoryEntry, MemoryLayer

log = logging.getLogger("goat2.memory.router")

__all__ = ["execute_route"]


async def _query_layer(
    layer: MemoryLayer,
    name: LayerName,
    role: AgentRole,
    query: str,
    limit: int,
    record: Callable[[LayerTiming], None],
) -> list[MemoryEntry]:
    """
    Call one layer's search, record timing and hit status.

    Pure execution — no side effects except the record callback.
    PyO3 candidate for the core query logic.
    """
    t0 = time.monotonic()
    try:
        results: list[MemoryEntry] = await layer.search(
            role, query, limit=limit, tags=None
        )
    except Exception as exc:
        log.warning("executor._query_layer: layer=%s error=%s", name, exc)
        results = []
    ms = Millis((time.monotonic() - t0) * 1_000.0)
    record(LayerTiming(layer=name, duration_ms=ms, hit=bool(results)))
    log.debug(
        "executor._query_layer: layer=%s dur_ms=%.2f hits=%d",
        name, float(ms), len(results),
    )
    return results


async def execute_route(
    decision: RoutingDecision,
    role: AgentRole,
    query: str,
    *,
    limit: int,
    layers: dict[LayerName, MemoryLayer],
    record: Callable[[LayerTiming], None],
) -> list[MemoryEntry]:
    """
    Execute routing decision against memory layers.

    Strategy based on confidence:
    - High (≥0.70): Query first layer only
    - Medium (0.40–0.69): Query first, fall through to second if empty
    - Low (<0.40): Fan-out all layers in parallel

    Returns deduplicated results sorted by recency (newest first).
    """

    def _get(name: LayerName) -> MemoryLayer:
        return layers[name]

    log.debug(
        "execute_route: layers=%s confidence=%.2f",
        decision.layers, float(decision.confidence),
    )

    if decision.confidence >= CONF_HIGH:
        return await _query_layer(
            _get(decision.layers[0]),
            decision.layers[0],
            role,
            query,
            limit,
            record,
        )

    if decision.confidence >= CONF_LOW and len(decision.layers) >= 2:
        res = await _query_layer(
            _get(decision.layers[0]),
            decision.layers[0],
            role,
            query,
            limit,
            record,
        )
        if res:
            return res
        return await _query_layer(
            _get(decision.layers[1]),
            decision.layers[1],
            role,
            query,
            limit,
            record,
        )

    # Full fan-out — parallel queries across all assigned layers
    all_results: list[list[MemoryEntry]] = await asyncio.gather(
        *[
            _query_layer(_get(name), name, role, query, limit, record)
            for name in decision.layers
        ]
    )
    seen: set[tuple[str, str]] = set()
    merged: list[MemoryEntry] = []
    for batch in all_results:
        for entry in batch:
            key = (str(entry.agent_role), str(entry.key))
            if key not in seen:
                seen.add(key)
                merged.append(entry)
    merged.sort(
        key=lambda e: e.metadata.get("created_at_ts", 0.0), reverse=True
    )
    return merged[:limit]
