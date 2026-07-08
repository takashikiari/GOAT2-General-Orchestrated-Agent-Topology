"""tests.test_person_entity_filter — reject false-positive PERSON entities.

Verified on real data (2026-07-08): 7 of 12 PERSON-tagged entities across 40
real mined queries were false positives — Romanian/English pronouns and
GOAT's own generic self-references, never genuine proper names. All 5
correct entities (Takashi x3, Laris x2) were capitalized; all 7 false
positives were lowercase (mine, tine, asistentul, orchestratorul, user).
"""
from __future__ import annotations

from memory.person_entity_filter import is_plausible_person


def test_accepts_capitalized_real_names():
    assert is_plausible_person("Takashi") is True
    assert is_plausible_person("Laris") is True


def test_rejects_romanian_pronouns():
    assert is_plausible_person("mine") is False
    assert is_plausible_person("tine") is False


def test_rejects_english_pronouns():
    assert is_plausible_person("me") is False
    assert is_plausible_person("you") is False


def test_rejects_goat_self_references_romanian():
    assert is_plausible_person("asistentul") is False
    assert is_plausible_person("orchestratorul") is False


def test_rejects_goat_self_references_english():
    assert is_plausible_person("user") is False
    assert is_plausible_person("assistant") is False


def test_rejects_lowercase_generic_words():
    """Capitalization is the primary signal — anything lowercase is rejected
    even if it isn't in either specific list (defense in depth)."""
    assert is_plausible_person("something") is False


def test_rejects_empty_string():
    assert is_plausible_person("") is False


def test_capitalized_stopword_still_rejected():
    """A pronoun capitalized only by sentence position ("Eu cred ca...")
    must still be rejected via the stopword check, not just capitalization."""
    assert is_plausible_person("Eu") is False
