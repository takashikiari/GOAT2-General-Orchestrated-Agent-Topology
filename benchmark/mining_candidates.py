"""benchmark.mining_candidates — information-dense entry selection for real-data mining (spec §4.2).

Pure function: filters exported snapshot entries down to candidates worth
generating a recall question for. Excludes short generic chit-chat; prefers
entries the enrichment pipeline (memory.enrichment.compute_importance) already
scored as important, when that metadata is present (older entries predate
enrichment and are judged on word count alone).
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
        importance = (entry.get("metadata") or {}).get("importance")
        if importance is not None and float(importance) < min_importance:
            continue
        candidates.append(entry)
    return candidates
