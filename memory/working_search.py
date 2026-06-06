from __future__ import annotations

import re

from memory.types import MemoryEntry

__all__ = ["_tokenize", "_score", "_entry_has_all_tags"]


def _tokenize(text: str) -> list[str]:
    # Pure — PyO3 candidate: fn tokenize(text: &str) -> Vec<String>
    """Split text into lowercase word tokens of length >= 2."""
    return [w for w in re.split(r"\W+", text.lower()) if len(w) >= 2]


def _score(query_terms: list[str], content: str, key: str) -> float:
    # Pure — PyO3 candidate: fn score(terms: &[&str], content: &str, key: &str) -> f32
    """
    Token-overlap relevance score in [0, 1.5].
    Normalised content hit rate + key-match bonus (x0.5).
    """
    if not query_terms:
        return 0.0
    cl           = content.lower()
    kl           = key.lower()
    content_hits = sum(1 for t in query_terms if t in cl)
    key_hits     = sum(1 for t in query_terms if t in kl)
    return content_hits / len(query_terms) + key_hits * 0.5


def _entry_has_all_tags(entry: MemoryEntry, required: list[str]) -> bool:
    # Pure — PyO3 candidate: fn entry_has_all_tags(tags: &[&str], required: &[&str]) -> bool
    raw = entry.metadata.get("tags") or []
    stored: set[str] = set(
        raw if isinstance(raw, list)
        else (t for t in str(raw).split(",") if t)
    )
    return all(t in stored for t in required)
