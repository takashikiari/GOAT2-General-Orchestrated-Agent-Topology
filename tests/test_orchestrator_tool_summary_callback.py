"""tests.test_orchestrator_tool_summary_callback — run() exposes tool_summary
via on_tool_summary, mirroring on_context_assembled, so callers (the
benchmark's groundedness judge) can see tool evidence without orchestrator.
run()'s return type changing.

Root cause (2026-07-09 manual review): the judge only ever saw memory context
blocks, never tool-call evidence, so a correct answer sourced from
read_file/shell_run/get_recent_logs was routinely flagged "unsupported by
context" even when verified byte-for-byte accurate against the real log file.
"""
from __future__ import annotations

import asyncio

from orchestrator.orchestrator import Orchestrator
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakePluginManager
from tests.test_agentic_loop import _Msg, _SeqLLM, _tc


class _Reg:
    def __init__(self, layers, llm, analytics) -> None:
        self.memory_layers = layers
        self.llm_client = llm
        self.memory_analytics = analytics
        self.plugin_manager = _FakePluginManager()


def _tool(name: str, result: str) -> ToolDefinition:
    async def handler(chat_id: str = "", **_kw) -> str:
        return result

    return ToolDefinition(
        name=name, description=name,
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
    )


def test_on_tool_summary_called_with_evidence_when_tools_used() -> None:
    msgs = [
        _Msg(tool_calls=[_tc("a1", "shell_run", '{"command":"ls"}')]),
        _Msg(content="done"),
    ]
    tools = [_tool("shell_run", "file.txt")]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())
    orch = Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    )
    captured: list[str] = []

    asyncio.run(orch.run("do it", "chat", on_tool_summary=captured.append))

    assert len(captured) == 1
    assert "shell_run" in captured[0]


def test_on_tool_summary_called_with_empty_string_when_no_tools_used() -> None:
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM([_Msg(content="no tools needed")]), _FakeAnalytics())
    orch = Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[],
    )
    captured: list[str] = []

    asyncio.run(orch.run("hi", "chat", on_tool_summary=captured.append))

    assert captured == [""]
