"""memory.result_merger — merge, dedupe, and score prefetch results.

Collects the results from the prefetch mechanisms (thematic, thematic_scoped,
topic_return), dedupes by ``message_id``, scores each with a fixed blend, and
sorts best-first. The score is::

    similarity * W_sim + recency * W_rec + access_count * W_acc

where ``similarity = 1/(1+distance)`` (Chroma L2 distance → 0-1, higher = closer;
exact structural matches carry distance 0 → similarity 1.0), ``recency`` is the
storage-timestamp age over a configurable window, and ``access_count`` is the
retrieval-popularity term (bumped on retrieval by ``EpisodicMemory.bump_access``),
capped at a reference count. All weights and normalisers come from
``config/memory.toml [prefetch]`` — no hardcoded values. The merged result list
carries a ``blended_score`` field so ``assemble_context`` can detect pre-scored
results and skip its own gap filter.
"""
from __future__ import annotations

import time

from memory.config import (
    PREFETCH_ACCESS_COUNT_REF,
    PREFETCH_RECENCY_WINDOW_DAYS,
    PREFETCH_SCORE_ACCESS_WEIGHT,
    PREFETCH_SCORE_RECENCY_WEIGHT,
    PREFETCH_SCORE_SIMILARITY_WEIGHT,
)

__all__ = ["merge_results"]

_RECENCY_WINDOW_SEC = PREFETCH_RECENCY_WINDOW_DAYS * 86400


def _similarity(distance: float) -> float:
    """Chroma L2 distance (lower = closer) → 0-1 similarity (higher = closer)."""
    d = max(0.0, float(distance))
    return 1.0 / (1.0 + d)


def _recency(timestamp: float, now: float) -> float:
    """Storage-time age over the window → 0-1 (1.0 just stored, 0.0 past the window)."""
    age = now - float(timestamp or 0.0)
    return max(0.0, 1.0 - age / _RECENCY_WINDOW_SEC)


def _access(access_count: float) -> float:
    """Retrieval popularity, capped at the reference count → 0-1."""
    return min(float(access_count or 0) / float(PREFETCH_ACCESS_COUNT_REF), 1.0)


def _blended(result: dict, now: float) -> float:
    """Weighted score for one result; weights from ``[prefetch]``.

    BM25-only results (from ``BM25Index.search``) intentionally omit the
    ``score`` key to signal "no semantic distance available". In that case
    the similarity term is 0.0 — recency and access-count still fire, and
    the cross-encoder makes the final relevance call.
    """
    meta = result.get("metadata", {}) or {}
    sim = _similarity(result["score"]) if "score" in result else 0.0
    rec = _recency(meta.get("timestamp", 0.0), now)
    acc = _access(meta.get("access_count", 0))
    return (
        PREFETCH_SCORE_SIMILARITY_WEIGHT * sim
        + PREFETCH_SCORE_RECENCY_WEIGHT * rec
        + PREFETCH_SCORE_ACCESS_WEIGHT * acc
    )


def _result_id(result: dict) -> object:
    """Stable dedup key: message_id, then Chroma id, then content hash."""
    meta = result.get("metadata", {}) or {}
    return meta.get("message_id") or result.get("id") or result.get("content")


def merge_results(groups: list[list[dict]], now: float | None = None) -> list[dict]:
    """Dedupe across ``groups`` by id, score each, sort best-first.

    Args:
        groups: result lists from each mechanism. Empty lists and missing
            fields degrade gracefully.
        now: reference "now" unix ts (defaults to ``time.time()``); injectable
            for deterministic tests.
    Returns:
        Deduped results sorted by ``blended_score`` descending, each carrying a
        ``blended_score`` field for ``assemble_context``'s pre-scored fast path.
    """
    now = now if now is not None else time.time()
    by_id: dict[object, dict] = {}
    for group in groups:
        for r in group:
            rid = _result_id(r)
            if rid is None:
                continue
            if rid not in by_id:
                by_id[rid] = r
    scored = []
    for r in by_id.values():
        score = _blended(r, now)
        r = dict(r)                       # copy so we don't mutate upstream results
        r["blended_score"] = score
        scored.append(r)
    scored.sort(key=lambda r: r["blended_score"], reverse=True)
    return scored