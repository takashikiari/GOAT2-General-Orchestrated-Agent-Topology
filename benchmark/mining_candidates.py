"""benchmark.mining_candidates — information-dense entry selection for real-data mining (spec §4.2).

Pure function: filters exported snapshot entries down to candidates worth
generating a recall question for. Excludes short generic chit-chat; prefers
entries the enrichment pipeline (memory.enrichment.compute_importance) already
scored as important, when that metadata is present (older entries predate
enrichment and are judged on word count alone).

Also requires a non-empty ``message_id`` in metadata. Confirmed on real data
(first end-to-end benchmark run, 2026-07-08): entries written before the
message_id field existed have no such key at all. real_data_mining.generate_case
used to fall back to the ChromaDB row id as ground-truth message_id for these,
but EpisodicMemory.search() returns metadata verbatim (no retroactive
fallback) — so that ground truth could never match a retrieved result,
producing an unfalsifiable hit@K=0% in prefetch_bench regardless of retrieval
quality. Excluding these candidates here means every mined case's message_id
is actually matchable.
"""
from __future__ import annotations

__all__ = ["select_candidates"]

_MIN_WORDS = 15
_MIN_IMPORTANCE = 0.3


def select_candidates(
    entries: list[dict], min_words: int = _MIN_WORDS, min_importance: float = _MIN_IMPORTANCE,
) -> list[dict]:
    """Filter exported entries to information-dense candidates for ground-truth mining."""
    candidates = []
    for entry in entries:
        content = (entry.get("content") or "").strip()
        if len(content.split()) < min_words:
            continue
        metadata = entry.get("metadata") or {}
        importance = metadata.get("importance")
        if importance is not None and float(importance) < min_importance:
            continue
        if not metadata.get("message_id"):
            continue
        candidates.append(entry)
    return candidates
