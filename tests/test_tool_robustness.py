"""tests.test_tool_robustness — what went wrong on 2026-11-12 cannot happen the same way again:
(1) a tool called with invented parameter names gets the real ones back instead of a Python TypeError,
(2) a tool round that ends in nothing but tool markup is answered once more without tools (or explained in Romanian),
(3) read_file reads a big file in parts (start_line / end_line),
(4) repo_git tells the model how git grep filters files."""
from __future__ import annotations

import asyncio
import json

from orchestrator import dsml_recovery
from orchestrator import orchestrator as orchestrator_module
from orchestrator.tool_args import argument_error
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _FakeAnalytics, _FakeLayers
from tests.test_agentic_loop import _Msg, _SeqLLM, _tc
from tests.test_dsml_tool_round import _dsml_block, _Reg, _run
from tools.goat_skills import read_file as read_file_mod
from tools.goat_skills import repo_git as repo_git_mod

READ_FILE = read_file_mod.build(None)[0]


def _schema_tool(name="read_file", calls=None):
    calls = calls if calls is not None else []

    async def handler(path: str, max_chars: int = 100, chat_id: str = "") -> str:
        calls.append(path)
        return "content"
    return ToolDefinition(name=name, description=name, handler=handler, parameters={
        "type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
        "required": ["path"]}), calls


def test_invented_or_missing_parameters_are_named_with_the_ones_the_tool_accepts():
    tool, _ = _schema_tool()
    msg = argument_error(tool, {"path": "a.py", "offset_start": 1, "offset_end": 9})
    assert msg == ("read_file: unknown parameter(s): offset_end, offset_start. It accepts: path (required), "
                   "max_chars. Call it again with those names.")
    assert "missing required parameter(s): path" in argument_error(tool, {"max_chars": 5})
    assert argument_error(tool, {"path": "a", "chat_id": "c"}) is None  # chat_id is injected by GOAT, not invented
    free = ToolDefinition("t", "t", {"type": "object", "properties": {}}, lambda **k: "")
    assert argument_error(free, {"anything": 1}) is None  # no listed parameters: not checked


def test_a_dsml_call_with_invented_parameters_reaches_the_model_as_a_clear_error_not_a_typeerror():
    tool, calls = _schema_tool()
    seen = []

    llm = _SeqLLM([
        _Msg(content=_dsml_block(("read_file", {"path": "memory/layers.py", "offset_start": 1, "offset_end": 50}))),
        _Msg(content="Am citit fișierul."),
    ])
    original = llm.chat.completions.create

    async def create(**kw):
        seen.append(kw["messages"][-1]["content"])
        return await original(**kw)
    llm.chat.completions.create = create
    reply = _run(_Reg(_FakeLayers(results=[]), llm, _FakeAnalytics()), [tool])
    assert calls == [] and reply == "Am citit fișierul."
    assert "unknown parameter(s): offset_end, offset_start" in seen[-1] and "It accepts: path (required)" in seen[-1]


def test_the_native_tool_path_checks_the_same_way():
    tool, calls = _schema_tool()
    llm = _SeqLLM([_Msg(tool_calls=[_tc("c1", "read_file", json.dumps({"path": "x", "bogus": 1}))]), _Msg(content="gata")])
    assert _run(_Reg(_FakeLayers(results=[]), llm, _FakeAnalytics()), [tool]) == "gata" and calls == []


def test_a_round_ending_in_only_tool_markup_gets_one_more_answer_without_tools(monkeypatch):
    monkeypatch.setattr(orchestrator_module, "AGENTIC_MAX_ITERATIONS", 2)
    tool, _ = _schema_tool()
    always = _dsml_block(("read_file", {"path": "x"}))
    # initial DSML, two DSML syntheses (the cap), then the no-tools call answers
    llm = _SeqLLM([_Msg(content=always), _Msg(content=always), _Msg(content=always), _Msg(content="Cache-ul e gol fiindcă…")])
    reply = _run(_Reg(_FakeLayers(results=[]), llm, _FakeAnalytics()), [tool])
    assert reply == "Cache-ul e gol fiindcă…"


def test_when_even_the_last_call_is_only_markup_the_fallback_is_romanian_and_says_what_ran():
    class _LLM:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    assert kw["messages"][-1]["content"] == dsml_recovery.FINAL_ANSWER_INSTRUCTION and "tools" not in kw
                    return type("R", (), {"choices": [type("C", (), {"message": _Msg(content=_dsml_block(("x", {})))})()]})()
    calls = [("repo_git", "{}", "r"), ("repo_git", "{}", "r"), ("get_recent_logs", "{}", "r")]
    text = asyncio.run(dsml_recovery.answer_without_tools(_LLM, "m", [], calls, "c", 0.1, 100))
    assert text.startswith("Am rulat 3 verificări (repo_git ×2, get_recent_logs)") and "„continuă”" in text
    assert "I gathered" not in text


def test_read_file_returns_the_lines_asked_for_with_a_header(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("".join(f"line {i}\n" for i in range(1, 101)))
    run = lambda **kw: asyncio.run(READ_FILE.handler(path=str(f), **kw))
    assert run(start_line=10, end_line=12) == "[lines 10-12 of 100]\nline 10\nline 11\nline 12\n"
    assert run(start_line=99).startswith("[lines 99-100 of 100]")
    assert "outside the file, which has 100 lines" in run(start_line=500)
    assert "whole numbers" in run(start_line="zece")
    assert run().startswith("line 1\n")  # no range: the file as before
    names = set(READ_FILE.parameters["properties"])
    assert {"start_line", "end_line"} <= names and "start_line" in READ_FILE.description


def test_repo_git_tells_the_model_how_git_grep_filters_files():
    tool = next(t for t in repo_git_mod.build(None) if t.name == "repo_git")
    assert "NU are --include" in tool.description and "-- *.py" in tool.description
    assert "grep -n cache_hit -- *.py" in tool.parameters["properties"]["command"]["description"]
