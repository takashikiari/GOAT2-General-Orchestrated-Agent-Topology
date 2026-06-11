"""Per-layer response-time and hit-rate tracking.

Rolling samples with percentile read helpers. Pure functions for
PyO3 compatibility where possible.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Final

from memory.router.types import LayerName, LayerTiming, _ALL_LAYERS

__all__ = ["LayerStats", "LayerStatsTracker"]

log = logging.getLogger("goat2.memory.router")

_SAMPLE_CAP: Final[int] = 1000  # max latency samples per layer (ring buffer)


def _percentile(samples: list[float], p: float) -> float:
    """
    Linear-interpolation percentile over a sorted sample list.

    Pure function — PyO3 candidate.
    """
    if not samples:
        return 0.0
    idx = (len(samples) - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(samples) - 1)
    return samples[lo] + (samples[hi] - samples[lo]) * (idx - lo)


@dataclass
class LayerStats:
    """
    Accumulated call metrics for one memory layer.

    Pure snapshot — PyO3 candidate. All fields are immutable after
    creation; use LayerStatsTracker to accumulate new data.
    """

    calls: int = 0
    total_ms: float = 0.0
    hits: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0

    @property
    def avg_ms(self) -> float:
        """Mean query latency in ms; 0.0 before any calls."""
        return self.total_ms / self.calls if self.calls else 0.0

    @property
    def hit_rate(self) -> float:
        """Fraction of calls returning ≥1 result; 0.0 before any calls."""
        return self.hits / self.calls if self.calls else 0.0


class LayerStatsTracker:
    """
    Mutable registry of LayerStats and latency samples.

    One entry per memory tier. Thread-unsafe; designed for asyncio
    single-threaded event loop usage.
    """

    def __init__(self) -> None:
        self._stats: dict[LayerName, LayerStats] = {
            layer: LayerStats() for layer in _ALL_LAYERS
        }
        self._samples: dict[LayerName, deque[float]] = {
            layer: deque(maxlen=_SAMPLE_CAP) for layer in _ALL_LAYERS
        }
        log.debug("LayerStatsTracker: initialised for layers=%s", list(_ALL_LAYERS))

    def record(self, timing: LayerTiming) -> None:
        """
        Incorporate one completed-query timing into rolling stats.

        Updates call count, total duration, hit count, and appends
        latency sample to ring buffer.
        """
        s = self._stats[timing.layer]
        s.calls += 1
        s.total_ms += float(timing.duration_ms)
        if timing.hit:
            s.hits += 1
        self._samples[timing.layer].append(float(timing.duration_ms))
        log.debug(
            "LayerStatsTracker.record: layer=%s dur_ms=%.2f hit=%s",
            timing.layer, float(timing.duration_ms), timing.hit,
        )

    def get(self, layer: LayerName) -> LayerStats:
        """
        Return LayerStats with current percentiles computed from samples.

        Computes p50, p95, p99 from the rolling sample buffer.
        """
        s = self._stats[layer]
        samps = sorted(self._samples[layer])
        return LayerStats(
            calls=s.calls,
            total_ms=s.total_ms,
            hits=s.hits,
            p50_ms=_percentile(samps, 50.0),
            p95_ms=_percentile(samps, 95.0),
            p99_ms=_percentile(samps, 99.0),
        )

    def snapshot(self) -> dict[LayerName, LayerStats]:
        """
        Return a copy of all per-layer stats.

        Safe to pass to pure routing functions. Each LayerStats
        includes computed percentiles from current samples.
        """
        return {layer: self.get(layer) for layer in _ALL_LAYERS}
