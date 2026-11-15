"""Phase 0 of the prefetch feedback (brain/usage.py + Orchestrator.run, 2026-11-15): record which L3 docs were
injected into the prompt. Collected only: nothing reads it, the prefetch is unchanged.

- a use is counted once per doc per turn; the count fades with the brain's half-life;
- off ([brain] enabled or usage false) → nothing written, no store created;
- orchestrator: the originals packed into the prompt (provenance orig_after, wave-2 excluded, only real L3 ids)
  land in l3_usage; a failing usage write never costs the reply, and the turn itself is still recorded.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from brain import brain as b
from brain import usage as u
from brain.store import Brain
from orchestrator import orchestrator as orch_mod
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient

_H = 3600.0


def _brain(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})


def test_one_use_per_doc_per_turn_and_it_fades_with_the_half_life(tmp_path):
    brain = _brain(tmp_path)
    try:
        assert asyncio.run(u.record(brain, ["d1", "d1", "d2", ""], now=0.0)) == 2
        asyncio.run(u.record(brain, ["d1"], now=24 * _H))                 # one half-life later: 1 * 0.5 + 1
        got = asyncio.run(u.scores(brain, ["d1", "d2", "never"], now=24 * _H))
    finally:
        brain.close()
    assert got == {"d1": pytest.approx(1.5), "d2": pytest.approx(0.5)}  # 'never' absent: never used


def test_off_writes_nothing_and_creates_no_store(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "_instance", None)
    monkeypatch.setattr(b, "Brain", lambda *a, **kw: pytest.fail("off must not create a store"))
    for cfg in ({"enabled": False, "usage": True}, {"enabled": True, "usage": False}):
        monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, **cfg, "db_path": str(tmp_path / "x.sqlite")})
        assert asyncio.run(b.record_usage(["d1"])) == 0
    assert not (tmp_path / "x.sqlite").exists()


class _Layers(_FakeLayers):
    async def assemble_context(self, chat_id, budget=None, l3_results=None, facts=None, messages=None,
                               identity_prompt=None, temporal_center=None, provenance=None):
        if provenance is not None:   # what fit_originals_first reports: packed originals by message_id
            provenance.update(orig_after=["doc-A", "doc-B", "some content prefix"])
        return ["[Identity]\nx", "[Context recuperat din istoric]\nA\nB"], 2


@pytest.fixture
def turn(monkeypatch):
    l3 = [{"content": "A", "metadata": {"message_id": "doc-A"}, "mechanisms": ["semantic_global"]},
          {"content": "B", "metadata": {"message_id": "doc-B"}, "mechanisms": ["bm25"]},
          {"content": "C", "metadata": {"message_id": "doc-C"}, "mechanisms": ["bm25"]}]

    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        return list(l3), False, None, {}
    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)

    def run():
        async def go():
            o = Orchestrator(_Layers(), _LLMClient(_Completions("ok")), _FakePluginManager(), _FakeAnalytics())
            reply = await o.run("Ce am zis de cache?", "42")
            await o.drain_background()
            return reply
        return asyncio.run(go())
    return run


@pytest.fixture
def brain_on(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "usage": True,
                                      "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    yield tmp_path / "brain.sqlite"
    if b._instance is not None:
        b._instance.close()


def _usage(path):
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute("SELECT doc_id FROM l3_usage"))
    finally:
        con.close()


def test_the_injected_originals_are_recorded_and_nothing_else(turn, brain_on):
    assert turn() == "ok"
    assert _usage(brain_on) == ["doc-A", "doc-B"]          # doc-C was retrieved but not packed; no content-prefix keys


def test_a_failing_usage_write_never_costs_the_reply(monkeypatch, turn, brain_on):
    async def boom(*a, **kw):
        raise RuntimeError("usage table down")
    monkeypatch.setattr(b, "record_usage", boom)
    assert turn() == "ok"
    con = sqlite3.connect(brain_on)
    try:
        assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 2     # the turn itself still recorded
    finally:
        con.close()
