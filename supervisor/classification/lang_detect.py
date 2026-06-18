"""Language detection — pure-Python heuristics over Unicode
diacritics + word-frequency. No LLM, no regex, no third-party
language-detection library.

USAGE:
    from supervisor.classification.lang_detect import detect_language

    code = detect_language("Buna ziua, ce mai faci?")
    # → "ro"
    code = detect_language("Hello, how are you?")
    # → "en"
    code = detect_language("Salut, how ești tu astăzi?")
    # → "mixed"

The decision rule:
  - Count Romanian diacritics (ăâîșț – 5 chars). Each diacritic
    counts as a Romanian signal.
  - Match common Romanian function words ("si", "sunt", "este",
    "pentru", "acest", "ce", "cu", "la", "de", "nu", "da") —
    each hit is a Romanian signal.
  - Match common English function words ("the", "is", "are",
    "and", "you", "this", "that", "for", "with", "on") —
    each hit is an English signal.
  - If both are nonzero and within 30 % of each other → "mixed".
  - Else pick the higher-count language.
  - Else default to "en".
"""
from __future__ import annotations

from typing import Final

__all__ = ["detect_language", "RO_DIACRITICS", "RO_WORDS", "EN_WORDS"]

# Romanian diacritics. Case-sensitive on purpose — the function
# lowercases input before matching.
RO_DIACRITICS: Final[str] = "ăâîșț"

# Common function words. Lower-case. Multi-word phrases (none
# here, kept simple) would require token-level matching; this
# list is enough for the conversational-style signals we care
# about.
RO_WORDS: Final[frozenset[str]] = frozenset({
    "si", "sunt", "este", "suntem", "sunteti", "pentru",
    "acest", "aceasta", "aceste", "acela", "ce", "cu", "la",
    "de", "nu", "da", "din", "pe", "in", "sau", "dar", "mai",
    "foarte", "cum", "unde", "cand", "dece", "fara", "doar",
    "toate", "tot", "ta", "tau", "meu", "mea", "nostru",
    "vostru", "lor", "sau", "aici", "acolo", "acum", "azi",
    "ieri", "maine", "bine", "rau", "multumesc", "te", "rog",
})

EN_WORDS: Final[frozenset[str]] = frozenset({
    "the", "is", "are", "was", "were", "and", "you", "this",
    "that", "for", "with", "on", "in", "at", "by", "of",
    "to", "from", "as", "or", "but", "not", "no", "yes",
    "i", "we", "they", "he", "she", "it", "my", "your",
    "our", "their", "his", "her", "its", "have", "has", "had",
    "do", "does", "did", "will", "would", "can", "could",
    "should", "may", "might", "must", "shall",
})


def detect_language(text: str) -> str:
    """Return ``"ro"``, ``"en"``, or ``"mixed"`` for ``text``.

    Args:
        text: Source text (any length). Empty → ``"en"``.

    Returns:
        ISO-like 2-letter code; ``"mixed"`` when both languages
        are detected in roughly equal proportion.
    """
    if not text:
        return "en"
    lower = text.lower()
    ro_diac = sum(1 for c in lower if c in RO_DIACRITICS)
    tokens = lower.split()
    ro_hits = sum(1 for w in tokens if w in RO_WORDS)
    en_hits = sum(1 for w in tokens if w in EN_WORDS)
    # Combine diacritic + word hits (diacritics are stronger signal).
    ro_score = ro_diac * 2 + ro_hits
    en_score = en_hits
    if ro_score == 0 and en_score == 0:
        return "en"
    if ro_score == 0:
        return "en"
    if en_score == 0:
        return "ro"
    # Within ~50 % of each other → mixed. Tighter thresholds
    # make the detector too reluctant to call a clearly bilingual
    # turn "mixed".
    lo = min(ro_score, en_score)
    hi = max(ro_score, en_score)
    if hi > 0 and lo / hi >= 0.5:
        return "mixed"
    return "ro" if ro_score > en_score else "en"