"""brain/mind_facts.py: the L1 facts in [În minte] — rules always, knowledge only when the message is about it."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from brain import mind_facts as mf
from brain import understand as u
from tests.test_brain_mind_expose import FakeClient, FakeEpisodic, _expose, on  # noqa: F401 - `on` is a fixture

FACTS = {"emoji_style": "Laris vrea emoji în fiecare mesaj.",
         "brother_name": "Fratele lui Laris se numește Ionuț.",
         "machine_lenovo": "Laptopul lui Laris: Lenovo L470.",
         "new_rule": "Nu-i spune „șefule”."}


def _classified(brain):
    mf.save(brain._db, {"emoji_style": "regulă", "brother_name": "cunoștință", "machine_lenovo": "cunoștință"}, 1.0)


def _section(block, title):
    if f"{title}:\n" not in block:
        return ""
    return block.split(f"{title}:\n", 1)[1].split("\n\n", 1)[0]


def test_classification_answer_is_validated():
    keys = ["a", "b", "c"]
    got = mf.parse({"a": "regulă", "b": "cunoștință", "c": "altceva", "zzz": "regulă", "d": 3}, keys)
    assert got == {"a": "regulă", "b": "cunoștință"}
    assert mf.parse(None, keys) == {}


def test_classify_asks_once_for_the_unclassified_and_keeps_the_rest_unclassified(on):  # noqa: F811
    _classified(on)
    client = FakeClient({"new_rule": "regulă", "brother_name": "regulă"})
    assert asyncio.run(mf.classify(on, client, FACTS)) == 1
    prompt = client.calls[0]["messages"][-1]["content"]
    assert "new_rule: Nu-i spune" in prompt and "brother_name" not in prompt   # only the unclassified are asked
    assert mf.kinds(on._db)["new_rule"] == "regulă"
    assert mf.kinds(on._db)["brother_name"] == "cunoștință"                     # never re-decided
    assert asyncio.run(mf.classify(on, client, FACTS)) == 0 and len(client.calls) == 1  # nothing left: no call
    assert asyncio.run(mf.classify(on, FakeClient("nu e json"), {**FACTS, "x": "y"})) == 0
    assert "x" not in mf.kinds(on._db)


def test_unclassified_is_a_rule():
    rules, knowledge = mf.split(FACTS, {"brother_name": "cunoștință", "machine_lenovo": "cunoștință",
                                        "gone_key": "cunoștință"})
    assert set(rules) == {"emoji_style", "new_rule"} and set(knowledge) == {"brother_name", "machine_lenovo"}


def test_brother_message_shows_the_brother_fact_and_css_does_not(on):  # noqa: F811
    _classified(on)
    epi = FakeEpisodic({})
    brother = FakeClient({"about": [], "names": [], "asks_past": False, "facts": ["brother_name", "emoji_style"]})
    block, shown, _ = _expose(brother, epi, "cum îl cheamă pe fratele meu?", [], FACTS)
    assert "brother_name: Fratele lui Laris" in _section(block, "Ce știu")
    assert "machine_lenovo" not in block                                        # knowledge not asked about
    assert "emoji_style: Laris vrea emoji" in _section(block, "Reguli")         # a rule is always there
    assert "new_rule: Nu-i spune" in _section(block, "Reguli")                  # unclassified = rule
    assert not shown                                                             # facts are not memories shown
    prompt = brother.calls[0]["messages"][-1]["content"]
    assert "brother_name — Fratele lui Laris" in prompt and "emoji_style —" not in prompt
    css = FakeClient({"about": ["CSS"], "names": [], "asks_past": False, "facts": []})
    block, _, _ = _expose(css, epi, "de ce nu merge CSS-ul?", [], FACTS, chat="43")
    assert "Ce știu" not in block and "brother_name" not in block
    assert "emoji_style" in _section(block, "Reguli") and "new_rule" in _section(block, "Reguli")


def test_a_relevant_fact_stays_in_mind_for_the_follow_up(on):  # noqa: F811
    _classified(on)
    epi = FakeEpisodic({})
    _expose(FakeClient({"about": [], "asks_past": False, "facts": ["brother_name"]}), epi, "fratele meu?", [], FACTS)
    nothing = FakeClient({"about": [], "asks_past": False, "facts": []})
    block, _, _ = _expose(nothing, epi, "și?", [], FACTS)
    assert "brother_name" in _section(block, "Ce știu")                         # carried into the next turn
    for _ in range(3):
        block, _, _ = _expose(nothing, epi, "altceva", [], FACTS)
    assert "brother_name" not in block                                           # faded out of mind


def test_a_name_found_in_a_fact_is_not_missing(on):  # noqa: F811
    _classified(on)
    asks = FakeClient({"about": [], "names": ["Ionuț", "Bucegi"], "asks_past": True, "facts": []})
    block, _, _ = _expose(asks, FakeEpisodic({}), "ce știi de Ionuț și Bucegi?", [], FACTS)
    assert "nu ai nimic despre Bucegi" in block and "nu ai nimic despre Ionuț" not in block


def test_message_facts_are_validated_against_the_keys_shown():
    body = json.dumps({"about": [], "names": [], "asks_past": False, "facts": ["brother_name", "nope", 3]})
    got = asyncio.run(u.understand_message(FakeClient(body), "x", [], {"brother_name": "Ionuț"}))
    assert got["facts"] == ["brother_name"]
    got = asyncio.run(u.understand_message(FakeClient(body), "x", []))
    assert got["facts"] == []


def test_the_sleep_tick_classifies_the_facts_in_the_background(on, monkeypatch):  # noqa: F811
    from brain import mind_sleep as ms
    from telegram_interface import _brain_sleep as bs
    monkeypatch.setattr(bs._brain, "mind_on", lambda: True)
    order = []

    async def slice_(brain, episodic, client, limit, **kw):
        order.append(dict(mf.kinds(on._db)))
        return 0
    monkeypatch.setattr(ms, "encode_slice", slice_)

    class Layers:
        async def get_identity_and_facts(self):
            return dict(FACTS)
    client = FakeClient({k: "cunoștință" if k.startswith(("brother", "machine")) else "regulă" for k in FACTS})
    tick = bs._mind_tick(SimpleNamespace(episodic_memory=None, llm_client=client, memory_layers=Layers()))

    async def go():
        await tick()
        await tick.running[0]
    asyncio.run(go())
    assert order == [{"emoji_style": "regulă", "brother_name": "cunoștință", "machine_lenovo": "cunoștință",
                      "new_rule": "regulă"}]                                   # classified before the backfill
