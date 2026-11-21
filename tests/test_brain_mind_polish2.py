"""Polish wave 2 (Q2-Q6): misclassified rules recoverable, cue names light knowledge, name matching, open items once,
no tool-call junk as memories."""
from __future__ import annotations

from brain import mind_facts as mf
from tests.test_brain_mind_expose import FakeClient, FakeEpisodic, _expose, on  # noqa: F401 - `on` is a fixture


def test_names_match_whole_words_with_diacritics_folded():
    facts = {"brother_name": "Fratele lui Laris se numește Ionuț.", "report": "O analiză de cost."}
    assert mf.mentioned(facts, "Ionut") and mf.mentioned(facts, "IONUȚ") and mf.mentioned(facts, "ionuţ")
    assert not mf.mentioned(facts, "Ana")                       # "analiză" is not Ana
    assert not mf.mentioned(facts, "Ion")                       # nor is "Ionuț" Ion
    assert mf.mentioned(facts, "brother")                       # a key's words count too
    assert not mf.mentioned(facts, "  ")
