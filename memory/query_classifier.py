"""memory.query_classifier — query classification for the prefetch daemon.

Two independent mechanisms evaluate every query; neither gates whether prefetch
runs at all (the daemon always runs, timeout is the only blocker):

  1. Thematic  — always 1.0; pure semantic ChromaDB search, no gate.
  2. Specific-key — structural-form regex only (UUID, agent-{uuid}, word+number,
                 turn_/goat: keys). No regex on natural language. Score 1.0 when
                 at least one structural key is present, else 0.

``classify_query`` returns the two scores; ``extract_structural_keys`` returns
the matched keys the specific-key mechanism retrieves by.
"""
from __future__ import annotations

import re

__all__ = ["classify_query", "extract_structural_keys"]

# --- Structural-form regexes (NOT natural language) -------------------------
# Full UUID: 8-4-4-4-12 hex.
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# agent-{uuid} reference.
_AGENT_UUID = re.compile(r"\bagent-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# "word+number" structural label: alpha-5675, alpha_5675, alpha 5675, turn_0001.
_WORD_NUM = re.compile(r"\b[a-z]{2,}[-_ ]\d{2,}\b", re.I)
# Explicit key forms: turn_0001, goat:user_profile.
_STRUCT_KEY = re.compile(r"\b(?:turn_\d+|goat:[a-z_]+)\b", re.I)


def extract_structural_keys(query: str) -> list[str]:
    """Return the deduped structural keys found in ``query`` (case preserved).

    Only structural forms are matched — UUID, agent-{uuid}, word+number, and
    explicit turn_/goat: keys. No natural-language regex. Order is stable
    (UUID first, then word-number / explicit keys in match order).
    """
    seen: set[str] = set()
    keys: list[str] = []
    for rx in (_UUID, _AGENT_UUID, _WORD_NUM, _STRUCT_KEY):
        for m in rx.findall(query):
            if m not in seen:
                seen.add(m)
                keys.append(m)
    return keys


def classify_query(query: str) -> dict[str, float]:
    """Score the two prefetch mechanisms for ``query``; each in [0, 1].

    Returns ``{"thematic", "specific_key"}``. Thematic is always 1.0;
    specific_key is 1.0 when at least one structural key is present.
    No confidence gate — the daemon runs all mechanisms whose score is > 0.
    """
    specific_key = 1.0 if extract_structural_keys(query) else 0.0
    return {"thematic": 1.0, "specific_key": specific_key}
