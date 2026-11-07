"""agent-coord turns: memory searches the messages' own text, not the digest's instruction header (2026-11-07)."""
from __future__ import annotations

import asyncio

from orchestrator import orchestrator as orch_mod
from orchestrator.orchestrator import Orchestrator
from telegram_interface._coord_poller import format_digest, retrieval_text
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient

MSGS = [{"from": "lenovo/agent-coord/ee1c5e", "to": "lenovo/goat/superv", "text": "Ce am decis despre Letta autostart?"}]


class _Spy(_FakeLayers):
    def __init__(self):
        super().__init__(results=[])
        self.embedded = []

    async def embed_query(self, query):
        self.embedded.append(query)
        return await super().embed_query(query)


def _run(monkeypatch, retrieval_query=None):
    seen = {}

    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        seen["retrieve"] = query
        return [], False, None, {}

    async def fake_prefetch(layers, chat_id, query, *a, **kw):
        seen["prefetch"] = query

    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(orch_mod, "run_prefetch_and_save", fake_prefetch)
    layers = _Spy()
    llm = _LLMClient(_Completions("ok"))
    digest = format_digest(MSGS)

    async def go():
        o = Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())
        r = await o.run(digest, "c", retrieval_query=retrieval_query)
        await asyncio.sleep(0)  # let the post-turn prefetch task start
        return r
    asyncio.run(go())
    return seen, layers, digest


def test_retrieval_text_is_only_the_messages():
    t = retrieval_text(MSGS + [{"text": ""}, {"text": "a doua\nlinie"}])
    assert t == "Ce am decis despre Letta autostart?\na doua ⏎ linie"
    assert "push, merge, deploy" in format_digest(MSGS) and "push, merge, deploy" not in t


def test_memory_uses_the_retrieval_query_prompt_and_storage_keep_the_digest(monkeypatch):
    q = retrieval_text(MSGS)
    seen, layers, digest = _run(monkeypatch, q)
    assert layers.embedded == [q] and seen["retrieve"] == q and seen["prefetch"] == q
    user_saved = [m["content"] for m in layers.saved if m["role"] == "user"]
    assert user_saved == [digest]  # what is stored / sent as the user turn is unchanged


def test_without_retrieval_query_behaviour_is_unchanged(monkeypatch):
    seen, layers, digest = _run(monkeypatch, None)
    assert layers.embedded == [digest] and seen["retrieve"] == digest
    seen, layers, digest = _run(monkeypatch, "   ")  # blank falls back to the intent
    assert seen["retrieve"] == digest
