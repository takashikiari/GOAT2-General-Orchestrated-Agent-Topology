"""memory.content_filter — T7: keep near-empty L3 entries out of the rerank.

Entries like "🤦", "🤣🤣🤣" or "Ce?" carry no retrievable information but, being tiny, can score high on vague
queries and take a slot in the prompt. They are dropped before the cross-encoder when they have fewer than
``min_alnum`` letters/digits (any script, so Romanian diacritics count).
"""
from __future__ import annotations

__all__ = ["alnum_count", "drop_tiny"]


def alnum_count(text: str | None) -> int:
    return sum(1 for ch in (text or "") if ch.isalnum())


def drop_tiny(results: list[dict], min_alnum: int) -> list[dict]:
    if min_alnum <= 0:
        return results
    return [r for r in results if alnum_count(r.get("content")) >= min_alnum]
