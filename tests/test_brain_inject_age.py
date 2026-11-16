"""T33 (Laris, 2026-11-16): a promise older than 24 h is no longer shown in the prompt block; it stays in the store.

The live case: "Confirmarea cifrelor vine la 16:21" stayed in GOAT's prompt as "ai promis" long after the numbers
had come. Unanswered questions are not affected; None = no limit (the old behaviour).
"""
from __future__ import annotations

import asyncio
import json

from brain import brain as b
from brain.inject import HEADER, format_block
from orchestrator import orchestrator as orch_mod
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient

_NOW = 1_800_000_000.0
_H = 3600.0


def _p(text, hours_ago, kind="promised"):
    return {"kind": kind, "event": text, "ts": str(_NOW - hours_ago * _H), "text": text}


def test_a_promise_older_than_24h_is_left_out_a_fresh_one_stays():
    block = format_block([_p("Revin cu cifrele azi.", 2), _p("Confirmarea vine la 16:21.", 25)], 600, 24, now=_NOW)
    assert "Revin cu cifrele azi." in block and "Confirmarea vine" not in block


def test_only_old_promises_means_no_block_at_all():
    assert format_block([_p("Confirmarea vine la 16:21.", 25)], 600, 24, now=_NOW) == ""


def test_an_old_unanswered_question_is_still_shown():
    block = format_block([_p("Când vine raportul?", 48, kind="unanswered")], 600, 24, now=_NOW)
    assert "Când vine raportul?" in block


def test_the_boundary_and_no_limit():
    assert "aproape" in format_block([_p("aproape revin azi", 23.9)], 600, 24, now=_NOW)
    assert "vechi" in format_block([_p("vechi revin azi", 100)], 600, None, now=_NOW)       # None = no limit


def test_end_to_end_the_prompt_does_not_carry_a_promise_older_than_24h(monkeypatch, tmp_path):
    import time

    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        return [], False, None, {}
    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "inject": True,
                                      "db_path": str(tmp_path / "brain.sqlite")})          # default limit: 24 h
    monkeypatch.setattr(b, "_instance", None)
    now = time.time()
    items = [{"kind": "promised", "event": "n", "ts": str(now - 2 * _H), "text": "Rulez testele și revin azi."},
             {"kind": "promised", "event": "o", "ts": str(now - 30 * _H), "text": "Confirmarea vine la 16:21."}]
    brain = b.get_instance()
    brain._db.execute("INSERT INTO nucleus VALUES (?, ?, ?, ?)", ("42", "human", json.dumps(items), now))
    brain._db.commit()
    seen = []

    class _Rec(_Completions):
        async def create(self, **kw):
            seen.append(kw["messages"][0]["content"])
            return await super().create(**kw)

    async def go():
        o = Orchestrator(_FakeLayers(), _LLMClient(_Rec("ok")), _FakePluginManager(), _FakeAnalytics())
        await o.run("Ce mai e deschis?", "42")
        await o.drain_background()
    try:
        asyncio.run(go())
    finally:
        brain.close()
    assert HEADER in seen[0] and "Rulez testele și revin azi." in seen[0]
    assert "Confirmarea vine la 16:21." not in seen[0]
