"""Activation lanes (2026-11-12): human / channel / dag keep separate thread state on one chat_id.

Before: agent-coord digests (bot.py, admin_chat_id) and DAG completions ran orchestrator.run() on
Laris's chat_id and read/wrote the SAME ``activation:{chat_id}`` — the live log showed one topic and
one turn counter advancing across Laris's and the channel's turns (17 -> 21), so a 3k-char digest
dragged the human thread's centroid. Now every caller names its lane and each lane has its own key.
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator import orchestrator as orch_mod
from orchestrator.activation_manager import update_activation
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient

CHAT = "1912576407"
EMB = {"wifi parola acasa": [1.0, 0.0, 0.0], "raport build ci": [0.0, 1.0, 0.0],
       "workflow terminat": [0.0, 0.0, 1.0], "si parola de la birou": [0.9, 0.1, 0.0]}


class _LaneLayers(_FakeLayers):
    """Fake layers with a real per-(chat, lane) activation store and fixed embeddings."""

    def __init__(self):
        super().__init__(results=[])
        self.store: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str]] = []

    async def embed_query(self, query):
        return EMB[query]

    async def get_activation(self, chat_id, lane="human"):
        self.calls.append(("get", lane))
        return self.store.get((chat_id, lane))

    async def set_activation(self, chat_id, activation, lane="human"):
        self.calls.append(("set", lane))
        self.store[(chat_id, lane)] = activation
        return True


def _orchestrator(monkeypatch, layers):
    async def fake_retrieve(layers_, chat_id, query, state, activation, topic_return_id=None):
        return [], False, None, {}

    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)
    return Orchestrator(layers, _LLMClient(_Completions("ok")), _FakePluginManager(), _FakeAnalytics())


def test_channel_and_dag_turns_do_not_move_the_human_thread(monkeypatch):
    """Human turn, then a channel digest and a DAG summary on the SAME chat_id, then a human
    follow-up: the follow-up must see the first human turn's centroid and counter, untouched."""
    layers = _LaneLayers()
    o = _orchestrator(monkeypatch, layers)

    async def go():
        def snap():
            a = layers.store[(CHAT, "human")]
            return id(a), list(a.centroid), a.turn_count, a.topic_id

        await o.run("wifi parola acasa", CHAT)
        await o.drain_background()
        after_first = snap()
        await o.run("digest text", CHAT, retrieval_query="raport build ci", lane="channel")
        await o.drain_background()
        await o.run("workflow terminat", CHAT, lane="dag")
        await o.drain_background()
        before_followup = snap()
        await o.run("si parola de la birou", CHAT)
        await o.drain_background()
        return after_first, before_followup

    first, seen = asyncio.run(go())
    assert seen == first                                     # channel + DAG never wrote the human lane
    assert seen[1] == [1.0, 0.0, 0.0] and seen[2] == 1
    assert layers.store[(CHAT, "channel")].centroid == [0.0, 1.0, 0.0]
    assert layers.store[(CHAT, "dag")].centroid == [0.0, 0.0, 1.0]
    assert layers.store[(CHAT, "human")].turn_count == 2     # the follow-up continued the human thread
    assert ("get", "channel") in layers.calls and ("set", "channel") in layers.calls
    assert ("get", "dag") in layers.calls and ("set", "dag") in layers.calls


def test_unknown_lane_is_refused_before_any_work(monkeypatch):
    layers = _LaneLayers()
    o = _orchestrator(monkeypatch, layers)
    with pytest.raises(ValueError):
        asyncio.run(o.run("wifi parola acasa", CHAT, lane="digest"))
    assert layers.calls == []


@pytest.mark.parametrize("state, prior", [("cold", None), ("drift", "act"), ("warm", "act")])
def test_update_activation_writes_the_given_lane_on_every_branch(state, prior):
    from memory.activation import Activation
    layers = _LaneLayers()
    act = Activation(centroid=[1.0, 0.0, 0.0], turn_count=2, ts=1.0, topic_id="t") if prior else None
    asyncio.run(update_activation(layers, CHAT, "q", [1.0, 0.0, 0.0], state, act, [], lane="dag"))
    assert layers.calls == [("set", "dag")]
    assert list(layers.store) == [(CHAT, "dag")]


def test_bot_callers_name_their_lane():
    """The channel digest and the DAG completion pass their lane explicitly; the plain Telegram
    message, the panel chat and voice keep the default human lane."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("telegram_interface", "bot.py").read_text()
    assert 'retrieval_query=retrieval_text(messages), lane="channel")' in src
    assert 'lane="dag")' in src
    assert "orchestrator.run(text, chat_id, on_context_assembled=injected.append)" in src  # Telegram: human
