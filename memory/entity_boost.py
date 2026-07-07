"""memory.entity_boost — GLiNER entity overlap re-scoring for prefetch results.

After merge_results produces blended scores, this pass extracts entities from
the user query via GLiNER (already warm from enrichment) and boosts results
whose stored metadata.entities overlap with the query entities.

Overlap formula:
    boost = (|query_entities ∩ memory_entities| / |query_entities|) * weight

blended_score is updated in-place on a copy; results are re-sorted. When
GLiNER finds no query entities the results are returned unchanged (no-op).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.gliner_extractor import GLiNERExtractor

# Additive boost on blended_score for full entity overlap.
# At weight=0.2: a result with 100% entity overlap gets +0.2 on top of its
# blended score (~0.3–0.7 typical), pushing it clearly ahead of non-matching
# results without dominating the ranking when the match is partial.
_ENTITY_BOOST_WEIGHT = 0.2


def _parse_stored_entities(meta_val: str) -> set[str]:
    """Parse comma-joined entity string from ChromaDB metadata to a lowercase set."""
    return {e.lower().strip() for e in (meta_val or "").split(",") if e.strip()}


async def entity_boost(
    query: str,
    results: list[dict],
    extractor: "GLiNERExtractor",
    weight: float = _ENTITY_BOOST_WEIGHT,
) -> list[dict]:
    """Re-score results by GLiNER entity overlap; re-sort blended_score descending.

    Args:
        query: The user message (passed to GLiNER for entity extraction).
        results: Pre-scored results from merge_results (blended_score set).
        extractor: Shared GLiNERExtractor instance (model already warm).
        weight: Additive boost weight for full overlap (default 0.2).
    Returns:
        Results with updated blended_score, sorted best-first. Unscored results
        (no blended_score field) are treated as score 0.0 for sorting only.
    """
    extracted = await extractor.extract(query)
    query_entities = {e.lower() for e in extracted.get("entities", [])}
    if not query_entities:
        return results

    out: list[dict] = []
    for r in results:
        meta = r.get("metadata", {}) or {}
        mem_entities = _parse_stored_entities(meta.get("entities", ""))
        overlap = len(query_entities & mem_entities)
        if overlap:
            boost = (overlap / len(query_entities)) * weight
            r = dict(r)
            r["blended_score"] = r.get("blended_score", 0.0) + boost
        out.append(r)

    out.sort(key=lambda r: r.get("blended_score", 0.0), reverse=True)
    return out
