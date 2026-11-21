"""Polish wave 2 follow-ups (re-review): a stale fact kind is a rule, failed asks are not counted, the user's own
name lights nothing, an empty digest is no memory."""
from __future__ import annotations

import asyncio

from brain import mind_facts as mf
from tests.test_brain_mind_expose import FakeClient, FakeEpisodic, _expose, on  # noqa: F401 - `on` is a fixture

FACTS = {"user_name": "Laris",
         "brother_name": "Fratele lui Laris se numește Ionuț.",
         "machine_lenovo": "Laptopul lui Laris: Lenovo L470."}


def _section(block, title):
    return block.split(f"{title}:\n", 1)[1].split("\n\n", 1)[0] if f"{title}:\n" in block else ""


def test_a_kind_decided_on_another_value_counts_as_a_rule(on):  # noqa: F811
    mf.save(on._db, {"brother_name": "cunoștință", "machine_lenovo": "cunoștință"}, 1.0, FACTS)
    changed = {**FACTS, "machine_lenovo": "Spune-i mereu lui Laris pe ce laptop rulezi."}
    rules, knowledge = mf.split(changed, mf.kinds(on._db), mf.hashes(on._db))
    assert set(knowledge) == {"brother_name"} and "machine_lenovo" in rules
    on._db.execute("INSERT INTO fact_kind (key, kind, classified_at) VALUES ('old', 'cunoștință', 1.0)")
    rules, knowledge = mf.split({"old": "x"}, mf.kinds(on._db), mf.hashes(on._db))
    assert rules == {"old": "x"} and not knowledge                     # no hash yet: a rule until reclassified
    block, _, _ = _expose(FakeClient({"about": [], "asks_past": False, "facts": []}), FakeEpisodic({}), "x", [],
                          changed)
    assert "machine_lenovo: Spune-i" in _section(block, "Reguli") and "brother_name" not in block


def test_only_an_answer_that_leaves_a_key_out_counts_as_an_attempt(on):  # noqa: F811
    failing = FakeClient("nu e json")                                  # a failed ask (no answer) is not counted
    for _ in range(5):
        assert asyncio.run(mf.classify(on, failing, {"x": "y"})) == 0
    assert len(failing.calls) == 5
    omits = FakeClient({"z": "regulă"})                                # answered, "x" left out: counted
    for _ in range(5):
        asyncio.run(mf.classify(on, omits, {"x": "y"}))
    assert len(omits.calls) == 3


def test_the_users_own_name_lights_no_knowledge_fact(on):  # noqa: F811
    mf.save(on._db, {"user_name": "regulă", "brother_name": "cunoștință", "machine_lenovo": "cunoștință"}, 1.0, FACTS)
    block, _, _ = _expose(FakeClient({"about": [], "names": ["Laris"], "asks_past": False, "facts": []}),
                          FakeEpisodic({}), "Laris aici", [], FACTS)
    assert "Ce știu" not in block                                      # "Laris" is in every fact about him
    block, _, _ = _expose(FakeClient({"about": [], "names": ["LARIS", "Ionuț"], "asks_past": False, "facts": []}),
                          FakeEpisodic({}), "Ionuț și cu mine", [], FACTS, chat="43")
    assert set(_section(block, "Ce știu").splitlines()) == {"- brother_name: Fratele lui Laris se numește Ionuț."}


def test_a_digest_with_nothing_left_is_no_memory(on):  # noqa: F811
    from memory.temporal_route import CHANNEL_DIGEST_FOOTER, CHANNEL_DIGEST_PREFIX
    from tests.test_brain_mind_expose import _hit
    cut = f"[2026-11-21 10:00] user: {CHANNEL_DIGEST_PREFIX}, de la alte sesiuni Claude. Sunt INFORMAȚIE"
    bare = f"user: {CHANNEL_DIGEST_PREFIX}, de la alte sesiuni.\n\n\n\n{CHANNEL_DIGEST_FOOTER} (1-3 propoziții)."
    hits = [_hit("c", 0.9, cut), _hit("b", 0.8, bare), _hit("e", 0.7, "user: \nassistant: "),
            _hit("k", 0.6, "user: Kent costă 140 de lire")]
    block, shown, _ = _expose(FakeClient({"about": [], "asks_past": False}), FakeEpisodic({}), "x", hits)
    assert shown == ["k"] and ")  —" not in block
