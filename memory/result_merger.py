"""memory.result_merger — fuse prefetch results with Reciprocal Rank Fusion.

RRF (Cormack et al. 2009) fuses multiple ranked lists by rank position rather
than absolute scores. Each mechanism contributes 1/(k+rank) per document;
documents appearing in multiple lists accumulate score. k=60 (original paper
default) dampens the impact of very high ranks without over-rewarding them.

No absolute retrieval scores (MiniLM L2 distance, BM25 BM25-score) are used —
those have incompatible distributions across mechanisms. CrossEncoder is the
final relevance arbiter after RRF selects the candidate pool.
"""
from __future__ import annotations

__all__ = ["merge_results"]

_K = 60  # RRF constant; 1/(k+rank) for rank=1 → 1/61 ≈ 0.016


def _result_id(result: dict) -> object:
    """Stable dedup key: message_id, then Chroma id, then content hash."""
    meta = result.get("metadata", {}) or {}
    return meta.get("message_id") or result.get("id") or result.get("content")


def merge_results(groups: list[list[dict]], now: float | None = None) -> list[dict]:
    """Fuse result lists from multiple mechanisms using RRF, deduped by id.

    Args:
        groups: result lists from each mechanism, each already ranked by that
            mechanism's own score (MiniLM distance, BM25, CrossEncoder, etc.).
        now: unused — kept for call-site compatibility.
    Returns:
        Deduped results sorted by RRF score descending, each carrying a
        ``blended_score`` field for ``assemble_context``'s pre-scored fast path.
    """
    rrf: dict[object, float] = {}
    best: dict[object, dict] = {}

    for group in groups:
        for rank, r in enumerate(group, start=1):
            rid = _result_id(r)
            if rid is None:
                continue
            rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (_K + rank)
            if rid not in best:
                best[rid] = r

    scored = []
    for rid, score in rrf.items():
        r = dict(best[rid])
        r["blended_score"] = score
        scored.append(r)
    scored.sort(key=lambda r: r["blended_score"], reverse=True)
    return scored
