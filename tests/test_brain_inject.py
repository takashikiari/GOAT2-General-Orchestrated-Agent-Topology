"""The brain's open-items nucleus in GOAT's system prompt (brain/inject.py + Orchestrator.run, 2026-11-15).

- format_block: one line per item, markdown stripped, max_chars respected, empty nucleus → "" (no block);
- brain.nucleus: off → [] and no store is created;
- orchestrator: brain off, or inject = false → the system prompt is byte-identical to the one without the brain;
  nucleus empty → no block; nucleus with items → the block is in the prompt, for the turn's real lane;
  the brain raises → the reply still comes, without the block.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from brain import brain as b
from brain.inject import HEADER, format_block
from orchestrator import orchestrator as orch_mod
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient

_ITEMS = [
    {"kind": "promised", "event": "e2", "ts": "1794751620.9", "text": "🐐 **Verific** logul și revin în 10 minute.\n\nDetalii"},
    {"kind": "unanswered", "event": "e1", "ts": "1794740000.0", "text": "Când vine raportul?"},
]


# --- format_block (pure) ---------------------------------------------------------------------------------------
def test_empty_nucleus_is_no_block_at_all():
    assert format_block([], 600) == ""
    assert format_block([{"kind": "promised", "ts": "1", "text": "   \n "}], 600) == ""


def test_one_line_per_item_without_markdown():
    block = format_block(_ITEMS, 600)
    lines = block.split("\n")
    assert lines[0] == HEADER and len(lines) == 3
    assert lines[1].startswith("- ai promis (") and lines[1].endswith("): 🐐 Verific logul și revin în 10 minute.")
    assert lines[2].startswith("- întrebare fără răspuns (") and lines[2].endswith("): Când vine raportul?")
    assert "**" not in block and "Detalii" not in block       # first line only, no markdown


def test_max_chars_is_never_exceeded():
    many = [{"kind": "promised", "ts": "1794751620", "text": "x" * 200} for _ in range(10)]
    for limit in (len(HEADER) + 60, 300, 600):
        block = format_block(many, limit)
        assert block and len(block) <= limit
    assert format_block(many, len(HEADER) + 10) == ""          # not even a meaningful line fits → no block


# --- brain.nucleus gate ----------------------------------------------------------------------------------------
def test_nucleus_off_returns_nothing_and_creates_no_store(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": False, "db_path": str(tmp_path / "x.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    monkeypatch.setattr(b, "Brain", lambda *a, **kw: pytest.fail("a disabled brain must not create a store"))
    assert asyncio.run(b.nucleus("42")) == []
    assert not (tmp_path / "x.sqlite").exists()


# --- orchestrator ----------------------------------------------------------------------------------------------
class _Recording(_Completions):
    def __init__(self):
        super().__init__("ok")
        self.system: list[str] = []

    async def create(self, **kw):
        self.system.append(kw["messages"][0]["content"])
        return await super().create(**kw)


@pytest.fixture
def system_prompt(monkeypatch):
    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        return [], False, None, {}
    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)

    def run(lane="human"):
        rec = _Recording()

        async def go():
            o = Orchestrator(_FakeLayers(), _LLMClient(rec), _FakePluginManager(), _FakeAnalytics())
            reply = await o.run("Ce mai e deschis?", "42", lane=lane)
            await o.drain_background()
            return reply
        reply = asyncio.run(go())
        return reply, rec.system[0]
    return run


def _brain_with(monkeypatch, tmp_path, items_by_lane: dict, **cfg):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "inject": True,
                                      "db_path": str(tmp_path / "brain.sqlite"), **cfg})
    monkeypatch.setattr(b, "_instance", None)
    brain = b.get_instance()
    for lane, items in items_by_lane.items():
        brain._db.execute("INSERT INTO nucleus VALUES (?, ?, ?, ?)", ("42", lane, json.dumps(items), 1.0))
    brain._db.commit()
    return brain


@pytest.fixture
def baseline(monkeypatch, system_prompt, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": False, "db_path": str(tmp_path / "off.sqlite")})
    return system_prompt()[1]


def test_brain_off_prompt_is_byte_identical(baseline, system_prompt):
    assert HEADER not in baseline
    assert system_prompt()[1] == baseline


def test_inject_false_prompt_is_byte_identical(monkeypatch, tmp_path, baseline, system_prompt):
    brain = _brain_with(monkeypatch, tmp_path, {"human": _ITEMS}, inject=False)
    try:
        assert system_prompt()[1] == baseline
    finally:
        brain.close()


def test_empty_nucleus_adds_no_block(monkeypatch, tmp_path, baseline, system_prompt):
    brain = _brain_with(monkeypatch, tmp_path, {"human": []})
    try:
        assert system_prompt()[1] == baseline
    finally:
        brain.close()


def test_nucleus_block_lands_in_the_prompt_for_the_turns_lane(monkeypatch, tmp_path, baseline, system_prompt):
    brain = _brain_with(monkeypatch, tmp_path, {"human": _ITEMS, "channel": []})
    try:
        human = system_prompt("human")[1]
        channel = system_prompt("channel")[1]
    finally:
        brain.close()
    assert HEADER in human and "Când vine raportul?" in human
    assert human.startswith(baseline.split("\n\n")[0])          # appended, the context blocks come first
    assert HEADER not in channel                                  # the channel lane has its own (empty) nucleus


def test_a_broken_brain_never_costs_the_reply(monkeypatch, tmp_path, baseline, system_prompt):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "inject": True,
                                      "db_path": str(tmp_path / "brain.sqlite")})

    async def boom(*a, **kw):
        raise RuntimeError("store down")
    monkeypatch.setattr(b, "nucleus", boom)
    monkeypatch.setattr(b, "record_turn", lambda *a, **kw: asyncio.sleep(0, 0))
    reply, prompt = system_prompt()
    assert reply == "ok" and prompt == baseline
