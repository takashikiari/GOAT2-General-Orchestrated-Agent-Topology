"""Detect the dominant natural language of a user intent — pure middleware.

NO LLM. Heuristics:

  - Count Romanian diacritics (ă â î ș ț, with both comma and cedilla
    forms of ș/ț). High share → ``ro``.
  - Match against a small common-words list for Romanian and English.
  - Mixed signals (both languages present in non-trivial amounts) →
    ``mixed``.
  - Default to ``en`` when no signal is found at all.

Returns one of three short codes so the caller can branch on a
single, stable identifier: ``"ro"`` | ``"en"`` | ``"mixed"``. The
old LLM-based implementation returned an English-language name
(``"English"`` / ``"Romanian"`` / …) which was brittle and slow;
the new heuristic answer is enough for the language directive
that ``task_prep.prepare_tasks`` prepends to each DAG task prompt.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Final

log = logging.getLogger("goat2.supervisor.classification")  # legacy logger name

__all__ = ["detect_language", "LANG_RO", "LANG_EN", "LANG_MIXED"]

LANG_RO: Final[str] = "ro"
LANG_EN: Final[str] = "en"
LANG_MIXED: Final[str] = "mixed"

# Romanian diacritics. ș/ț appear in both the comma-below form and
# the cedilla form in real-world text; treat both as Romanian.
_RO_DIACRITICS: Final[str] = "ăâîșț"
_RO_DIACRITICS_NFC: Final[str] = unicodedata.normalize("NFC", _RO_DIACRITICS)

# Common Romanian words — longer, higher-precision words. Short
# function words ("are", "a", "in", "pe") overlap with English
# and add noise, so we skip them. The diacritic check below
# provides the high-precision short-word signal.
_RO_WORDS: Final[frozenset[str]] = frozenset({
    "vreau", "trebuie", "mulțumesc", "multumesc", "săptămână",
    "saptamana", "acest", "această", "acestei", "acestor",
    "acolo", "aici", "salut", "bună", "buna", "noapte",
    "merge", "facem", "facut", "făcut", "făcut", "faci", "gândesc",
    "gindesc", "gândire", "când", "cand", "atunci", "deci",
    "totuși", "totusi", "însă", "insa", "aproape", "departe",
    "întrebare", "intrebare", "răspuns", "raspuns", "fiecare",
    "oricum", "oricând", "oricand", "ziua", "noapte",
    "merge", "astăzi", "astazi", "mâine", "mainе", "ieri",
})

# Common English words — only 4+ character words to avoid the
# short-word overlap (e.g. "are" / "in" / "on" are valid in both
# languages). The diacritic check is what catches Romanian at the
# short-word level.
_EN_WORDS: Final[frozenset[str]] = frozenset({
    "hello", "thanks", "please", "what", "when", "where",
    "which", "this", "that", "these", "those", "your", "have",
    "would", "could", "should", "might", "about", "there",
    "their", "they", "with", "from", "just", "like", "make",
    "know", "think", "want", "need", "help", "okay",
    "today", "tomorrow", "yesterday",
})

# Whitespace + basic punctuation tokenizer.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[\wăâîșțĂÂÎȘȚ]+", re.UNICODE)


def detect_language(intent: str) -> str:
    """Return ``"ro"`` / ``"en"`` / ``"mixed"`` for ``intent``.

    Pure heuristic. No LLM. Empty / whitespace-only input
    defaults to ``"en"`` (no signal → no language directive
    prepended in ``task_prep``).
    """
    if not intent or not intent.strip():
        return LANG_EN
    text = unicodedata.normalize("NFC", intent.lower())
    tokens = [t for t in _TOKEN_RE.findall(text) if len(t) > 1]
    if not tokens:
        return LANG_EN
    ro_diacritics = sum(1 for ch in text if ch in _RO_DIACRITICS_NFC)
    ro_hits = sum(1 for t in tokens if t in _RO_WORDS)
    en_hits = sum(1 for t in tokens if t in _EN_WORDS)
    log.debug(
        "detect_language: ro_diacritics=%d ro_hits=%d en_hits=%d tokens=%d",
        ro_diacritics, ro_hits, en_hits, len(tokens),
    )
    if ro_diacritics >= 2 or ro_hits >= 2:
        # Strong Romanian signal. If the English count is also
        # non-trivial, the message is mixed.
        if en_hits >= 2:
            return LANG_MIXED
        return LANG_RO
    if en_hits >= 1 and ro_hits == 0 and ro_diacritics == 0:
        return LANG_EN
    if ro_hits >= 1 and en_hits >= 1:
        return LANG_MIXED
    # No signal — default to English so the caller does not
    # prepend a Romanian directive for unknown content.
    return LANG_EN
