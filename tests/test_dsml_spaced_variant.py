"""DSML spelled with a space and 'calls' (2026-11-07) is recognised and never reaches the user.

Tag shapes copied from the two Telegram messages of 2026-11-07 11:41:48 / 11:42:38; the values
are invented (the real ones were private notes).
"""
from __future__ import annotations

import asyncio

from orchestrator import orchestrator as orchestrator_module
from orchestrator.dsml import DSML_BLOCK, DSML_INVOKE, DSML_PARAM, strip_dsml
from tests._orch_fakes import _FakeAnalytics, _FakeLayers
from tests.test_agentic_loop import _Msg, _SeqLLM, _tc
from tests.test_dsml_tool_round import _Reg, _run, _tool

SPACED = ('\n\n<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="store_memory">\n'
          '<｜｜DSML｜｜ parameter name="content" string="true">o notă inventată</｜｜DSML｜｜ parameter>\n'
          '</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>')


def test_spaced_variant_is_recognised_and_parsed():
    block = DSML_BLOCK.search(SPACED)
    assert block
    (name, body), = DSML_INVOKE.findall(block.group(1))
    assert name == "store_memory"
    assert DSML_PARAM.findall(body) == [("content", "true", "o notă inventată")]


def test_original_variant_still_recognised():
    old = '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="t">{"a": 1}</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>'
    assert DSML_BLOCK.search(old) and DSML_INVOKE.findall(old) == [("t", '{"a": 1}')]


def test_strip_removes_every_form_and_keeps_prose():
    assert strip_dsml("Gata." + SPACED) == "Gata."
    assert strip_dsml("Text <｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name=\"x\"> tăiat") == "Text"  # never closed
    assert strip_dsml("a <|DSML| invoke name=\"x\">b</|DSML| invoke> c") == "a b c"  # stray tags, ASCII bars
    assert strip_dsml("fără markup") == "fără markup" and strip_dsml("") == ""


def test_size_capped_synthesis_in_spaced_dsml_runs_tool_and_replies_clean(monkeypatch):
    """The 11:41 shape: one huge tool result → size cap → forced synthesis answers in spaced DSML."""
    monkeypatch.setattr(orchestrator_module, "TOOL_ROUND_MAX_OUTPUT_CHARS", 10)
    logs = _tool("get_recent_logs", "x" * 500)
    note = _tool("store_memory", "stored")
    msgs = [
        _Msg(tool_calls=[_tc("a1", "get_recent_logs")]),
        _Msg(content=SPACED),          # forced synthesis (tools withheld) asks for a tool in DSML
        _Msg(content="Am notat."),     # DSML round synthesis
    ]
    reg = _Reg(_FakeLayers(results=[]), _SeqLLM(msgs), _FakeAnalytics())
    reply = _run(reg, [logs, note])
    assert "DSML" not in reply and reply == "Am notat."
    assert note.calls and note.calls[0].get("content") == "o notă inventată"


def test_unexecutable_spaced_dsml_never_reaches_user_or_history():
    msgs = [_Msg(content="Uite:" + SPACED)] + [_Msg(content=SPACED)] * 10  # model never stops
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())
    reply = _run(reg, [])
    assert "DSML" not in reply and reply.strip()
    assert all("DSML" not in m["content"] for m in layers.saved)
