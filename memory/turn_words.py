"""memory.turn_words — how much a message actually says, for topic-shift decisions.

Measured on 124 real Laris turns (2026-11-06..07): short messages ("Da", "Ok", "Aprob", "De ce?", "🤣🤣",
"Merge si restart pr 29") have a median cosine to the thread centroid of 0.38 vs 0.55 for longer ones, so
the cosine alone cut threads on confirmations: 13 of the 16 cold turns at drift_cold=0.30 were not real topic
shifts. A message with fewer than ``min_words`` content words never breaks the thread on its own.
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["content_words"]

_WORD = re.compile(r"\w+")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def content_words(text: str | None) -> int:
    """Words of 3+ letters/digits, diacritics folded (so "ți", "da", "ok" and emoji do not count)."""
    return sum(1 for w in _WORD.findall(_fold(text or "")) if len(w) > 2)
