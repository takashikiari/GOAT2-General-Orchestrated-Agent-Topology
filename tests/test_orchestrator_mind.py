"""[mind] on: the prompt's memory is ONE [În minte] block (brain.mind_live.expose), the turn is learned by
mind_live.after_turn, GOAT has no memory-search tools (spec §3 „Fără cârje"). [mind] off: the turn is today's (R5)."""
from __future__ import annotations

import asyncio

import pytest

from brain import brain as b
from brain import live as _live
from brain import mind_live
from brain.inject import HEADER as NUCLEUS_HEADER
from memory.budget import estimate_tokens
from orchestrator import mind_hook
from orchestrator import orchestrator as orch_mod
from orchestrator.orchestrator import (
    _PROMOTE_MEMORY_GUIDANCE, _SEARCH_MEMORY_GUIDANCE, _STORE_MEMORY_GUIDANCE, Orchestrator,
)
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient

_L3 = [{"id": "m1", "content": "Laris a ales Kent", "blended_score": 0.9, "metadata": {"message_id": "m1"}},
       {"id": "m2", "content": "zbor anulat", "blended_score": 0.2, "metadata": {"message_id": "m2"}}]
_FACTS = {"nume": "Laris"}
_BLOCK = "[În minte]\nCe știu:\n- nume: Laris\n\nCe s-a întâmplat:\n- (2026-11-20) Laris a ales Kent — seamănă"
_TOOLS = ("search_memory", "read_l1", "store_memory", "promote_memory", "forget_fact")
_CHANNEL_DOC = "[2026-11-14T17:38:23+02:00] user: Mesaje noi pe canalul agent-coord, de la x"


class _Recording(_Completions):
    def __init__(self):
        super().__init__("ok")
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append(kw)
        return await super().create(**kw)


class _Layers(_FakeLayers):
    def __init__(self):
        super().__init__(blocks=["[Identity]\nYou are GOAT."])
        self.assembled: list[dict] = []

    async def get_identity_and_facts(self):
        return dict(_FACTS)

    async def assemble_context(self, chat_id, budget=None, l3_results=None, facts=None, messages=None,
                               identity_prompt=None, temporal_center=None, provenance=None):
        self.assembled.append({"budget": budget, "l3_results": list(l3_results or []), "facts": facts})
        return list(self._blocks), 0


def _tool(name):
    async def handler(**kw):
        return "x"
    return ToolDefinition(name, name, {"type": "object", "properties": {}}, handler)


@pytest.fixture
def turn(monkeypatch, tmp_path):
    """Runs one turn; returns (system content, offered tool names, layers, the LLM recorder, the reply)."""
    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        return list(_L3), False, None, {}
    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(b, "record_turn", lambda *a, **kw: asyncio.sleep(0, []))
    monkeypatch.setattr(b, "_instance", None)

    def run(lane="human"):
        rec, layers = _Recording(), _Layers()

        async def go():
            o = Orchestrator(layers, _LLMClient(rec), _FakePluginManager(), _FakeAnalytics(),
                             tools=[_tool(n) for n in _TOOLS])
            reply = await o.run("Ce am ales pentru Kent?", "42", lane=lane)
            await o.drain_background()
            return reply
        reply = asyncio.run(go())
        kw = rec.calls[0]
        names = [t["function"]["name"] for t in kw.get("tools", [])]
        return kw["messages"][0]["content"], names, layers, rec, reply
    return run


def _set(monkeypatch, tmp_path, brain_on: bool, mind: bool):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": brain_on, "inject": True,
                                      "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "MIND", {**b._MIND_DEFAULTS, "enabled": mind})


@pytest.fixture
def mind_calls(monkeypatch):
    calls = {"expose": [], "after": []}

    async def expose(client, episodic, chat_id, lane, message, l3_results, facts, budget_tokens):
        calls["expose"].append(dict(client=client, chat_id=chat_id, lane=lane, message=message,
                                    l3_results=list(l3_results), facts=dict(facts), budget=budget_tokens))
        return _BLOCK, ["m1"], {"m1": "Laris a ales Kent"}

    async def after_turn(client, chat_id, lane, turn_id, user, reply, shown, texts):
        calls["after"].append(dict(client=client, chat_id=chat_id, lane=lane, turn_id=turn_id, user=user,
                                   reply=reply, shown=list(shown), texts=dict(texts)))

    async def nucleus(chat_id, lane="human"):
        return [{"kind": "unanswered", "event": "e1", "ts": "1794740000.0", "text": "Când vine raportul?"}]

    async def old_recall(*a, **kw):
        calls.setdefault("old_recall", []).append(a)
        return a[1], {}

    async def old_after(*a, **kw):
        calls.setdefault("old_after", []).append(a)

    monkeypatch.setattr(mind_live, "expose", expose)
    monkeypatch.setattr(mind_live, "after_turn", after_turn)
    monkeypatch.setattr(b, "nucleus", nucleus)
    monkeypatch.setattr(orch_mod._brain_live, "recall_into", old_recall)
    monkeypatch.setattr(orch_mod._brain_live, "after_turn", old_after)
    return calls


@pytest.mark.parametrize("brain_on,mind", [(False, True), (True, False)])
def test_off_prompt_and_tools_unchanged(monkeypatch, tmp_path, turn, mind_calls, brain_on, mind):
    _set(monkeypatch, tmp_path, brain_on, mind)
    system, names, layers, _, reply = turn()
    assert reply == "ok"
    assert "[În minte]" not in system and mind_hook.MIND_GUIDANCE not in system
    for g in (_SEARCH_MEMORY_GUIDANCE, _STORE_MEMORY_GUIDANCE, _PROMOTE_MEMORY_GUIDANCE):
        assert g in system
    assert (NUCLEUS_HEADER in system) == brain_on              # the nucleus path is today's
    assert names == list(_TOOLS)                               # search_memory and read_l1 still offered
    assert layers.assembled == [{"budget": layers.assembled[0]["budget"], "l3_results": _L3, "facts": _FACTS}]
    assert mind_calls["expose"] == [] and mind_calls["after"] == []
    assert len(mind_calls.get("old_recall", [])) == 1   # off, recall_into runs as today (it self-gates)
    assert len(mind_calls.get("old_after", [])) == (1 if brain_on else 0)
    assert mind_hook.hidden_tools() == set()


def test_on_prompt_has_one_memory_block_and_no_search_tools(monkeypatch, tmp_path, turn, mind_calls):
    _set(monkeypatch, tmp_path, True, True)
    system, names, layers, rec, _ = turn()
    assert sum(p.startswith("[În minte]") for p in system.split("\n\n")) == 1 and _BLOCK in system and mind_hook.MIND_GUIDANCE in system
    for g in (_SEARCH_MEMORY_GUIDANCE, _STORE_MEMORY_GUIDANCE, _PROMOTE_MEMORY_GUIDANCE):
        assert g not in system
    assert NUCLEUS_HEADER not in system                        # open items live in [În minte] now
    assert names == ["store_memory", "promote_memory", "forget_fact"]
    # the context blocks carry neither L3 results nor L1 facts; expose gets retrieve()'s results and the facts (R15)
    (assembled,) = layers.assembled
    assert assembled["l3_results"] == [] and assembled["facts"] == {}
    (call,) = mind_calls["expose"]
    assert call["l3_results"] == _L3 and call["facts"] == _FACTS and call["lane"] == "human"
    assert call["client"] is not None and call["message"] == "Ce am ales pentru Kent?"
    assert call["budget"] == assembled["budget"] - estimate_tokens("[Identity]\nYou are GOAT.")
    assert "old_recall" not in mind_calls and "old_after" not in mind_calls


def test_on_after_turn_is_scheduled_with_shown_ids(monkeypatch, tmp_path, turn, mind_calls):
    _set(monkeypatch, tmp_path, True, True)
    _, _, layers, _, reply = turn()
    (after,) = mind_calls["after"]
    turn_id = layers.saved[0]["l3_id"]
    assert after == dict(client=mind_calls["expose"][0]["client"], chat_id="42", lane="human", turn_id=turn_id,
                         user="Ce am ales pentru Kent?", reply=reply, shown=["m1"],
                         texts={"m1": "Laris a ales Kent"})


def test_channel_lane_is_lived_when_on(monkeypatch, tmp_path, turn, mind_calls):
    _set(monkeypatch, tmp_path, True, False)
    assert _live._not_lived(_CHANNEL_DOC)                       # off: other sessions' traffic, as today
    _set(monkeypatch, tmp_path, True, True)
    assert not _live._not_lived(_CHANNEL_DOC)                   # on: agent-coord is lived memory
    turn(lane="channel")
    assert mind_calls["expose"][0]["lane"] == "channel" and mind_calls["after"][0]["lane"] == "channel"
