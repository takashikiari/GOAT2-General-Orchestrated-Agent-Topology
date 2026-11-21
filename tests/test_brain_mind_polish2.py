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


FACTS = {"emoji_style": "Laris vrea emoji în fiecare mesaj.",
         "brother_name": "Fratele lui Laris se numește Ionuț.",
         "machine_lenovo": "Laptopul lui Laris: Lenovo L470."}


def _section(block, title):
    return block.split(f"{title}:\n", 1)[1].split("\n\n", 1)[0] if f"{title}:\n" in block else ""


def test_a_fact_a_cue_name_points_to_is_shown(on):  # noqa: F811
    """Q3: "not missing" means shown: the name lights the fact even when the understanding did not list it."""
    mf.save(on._db, {"emoji_style": "regulă", "brother_name": "cunoștință", "machine_lenovo": "cunoștință"}, 1.0)
    asks = FakeClient({"about": [], "names": ["Ionut"], "asks_past": True, "facts": []})
    block, _, _ = _expose(asks, FakeEpisodic({}), "ce mai face Ionut?", [], FACTS)
    assert "brother_name: Fratele lui Laris" in _section(block, "Ce știu")
    assert "machine_lenovo" not in block and "nu ai nimic despre Ionut" not in block
    failed = FakeClient("nu e json")                           # understanding failed: no cues, nothing extra
    block, _, _ = _expose(failed, FakeEpisodic({}), "ce mai face Ionut?", [], FACTS, chat="43")
    assert "Ce știu" not in block and "emoji_style" in _section(block, "Reguli")
