"""memory.person_entity_filter — reject false-positive PERSON entities from GLiNER.

Verified on real data (2026-07-08): 7 of 12 PERSON-tagged entities across 40
real mined queries were false positives — pronouns and GOAT's own generic
self-references, never genuine proper names. Language-independent: no
hand-rolled grammar rules (a Romanian-only definite-article-suffix check was
considered and rejected — GOAT also handles English). Uses capitalization
(a convention shared by Romanian and English proper nouns) plus stopwordsiso,
an actively maintained multi-language stopword library, rather than
hand-written per-language pronoun lists.
"""
from __future__ import annotations

import stopwordsiso

__all__ = ["is_plausible_person"]

_STOPWORD_LANGS = ("ro", "en")

# Words GOAT itself uses to refer to its own components — not a general
# common-noun detector (unmaintainable in any language), just the small,
# stable set that recurs because GOAT's conversations are often about GOAT.
_GOAT_SELF_REFERENCES = {
    "user", "utilizator", "assistant", "asistent", "asistentul",
    "orchestrator", "orchestratorul", "system", "sistem", "sistemul",
    "bot", "goat",
}


def is_plausible_person(text: str) -> bool:
    """True unless ``text`` looks like a pronoun, GOAT self-reference, or a
    lowercase generic word rather than a genuine proper name.

    Known limitation: an English generic noun capitalized only by sentence
    position (rare, not observed in real data) would still pass — accepted
    as a documented residual gap rather than adding a fourth rule for it.
    """
    stripped = text.strip()
    if not stripped or not stripped[0].isupper():
        return False
    lowered = stripped.lower()
    if lowered in _GOAT_SELF_REFERENCES:
        return False
    if lowered in stopwordsiso.stopwords(_STOPWORD_LANGS):
        return False
    return True
