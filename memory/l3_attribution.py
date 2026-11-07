"""memory.l3_attribution — which injected L3 entries came from which retrieval mechanism.

The model only sees the L3 block as text, so usage_probe (which measures use from that text) cannot
tell a wave-2 neighbour from a normal hit. `pack_order` is the single definition of the order in which
fit_search_results packs results (so the n-th line of the block is the n-th packed result), and
`InjectedBlocks` carries the resulting per-mechanism ranks next to the blocks to log_usage — through
the existing on_context_assembled callback, per turn, with no global state.
"""
from __future__ import annotations

__all__ = ["pack_order", "mechanism_ranks", "InjectedBlocks"]


def pack_order(results: list[dict], temporal_center: float | None = None) -> list[dict]:
    """Temporal-tagged results first (closest to temporal_center, else oldest-first), then the rest."""
    temporal = [r for r in results if "temporal" in r.get("mechanisms", [])]
    if temporal_center is not None:
        temporal.sort(key=lambda r: abs(r["metadata"].get("timestamp", 0) - temporal_center))
    else:
        temporal.sort(key=lambda r: r["metadata"].get("timestamp", 0))
    temporal_ids = {id(r) for r in temporal}
    return temporal + [r for r in results if id(r) not in temporal_ids]


def mechanism_ranks(packed: list[dict], mechanism: str) -> list[int]:
    """1-based ranks (= line positions in the L3 block) of the packed results tagged with mechanism."""
    return [i + 1 for i, r in enumerate(packed) if mechanism in r.get("mechanisms", [])]


class InjectedBlocks(list):
    """The context blocks of one turn plus the provenance of their L3 entries (e.g. wave2_ranks)."""

    def __init__(self, blocks, provenance: dict | None = None):
        super().__init__(blocks)
        self.provenance = dict(provenance or {})
