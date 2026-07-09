"""tests.test_tool_round_output_cap — the tool round force-synthesizes once
cumulative tool-output size crosses TOOL_ROUND_MAX_OUTPUT_CHARS, independent
of AGENTIC_MAX_ITERATIONS.

Root cause (2026-07-09 incident): AGENTIC_MAX_ITERATIONS bounds round COUNT,
but nothing bounded cumulative tool-output SIZE. Each iteration resends the
full growing conversation, so a model issuing several large read_file/
shell_run calls could blow the resent conversation past the model's context
window well before the iteration cap fired — one benchmark turn hit 2.08M
tokens in one API call and was rejected outright. This test proves the size
backstop fires first when output is large, mirroring how
test_agentic_loop.py::test_cap_forces_synthesis_when_model_keeps_calling_tools
proves the iteration backstop.
"""
from __future__ import annotations

import asyncio

from orchestrator import orchestrator as orchestrator_module
from orchestrator.orchestrator import AGENTIC_MAX_ITERATIONS, Orchestrator
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakePluginManager
from tests.test_agentic_loop import _Msg, _SeqLLM, _tc


class _Reg:
    def __init__(self, layers, llm, analytics) -> None:
        self.memory_layers = layers
        self.llm_client = llm
        self.memory_analytics = analytics
        self.plugin_manager = _FakePluginManager()


def _big_tool(name: str, chars: int) -> ToolDefinition:
    async def handler(chat_id: str = "", **_kw) -> str:
        return "x" * chars

    return ToolDefinition(
        name=name, description=name,
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
    )


def test_size_cap_forces_synthesis_before_iteration_cap(monkeypatch) -> None:
    """A model that would keep calling tools every round (each queued response
    carries tool_calls) hits the size cap after 3 rounds of 100-char output
    each (cumulative 300 > cap 250) and is force-synthesized on the 4th call
    -- well short of the AGENTIC_MAX_ITERATIONS=6 iteration cap, which alone
    would have allowed up to 7 calls."""
    monkeypatch.setattr(orchestrator_module, "TOOL_ROUND_MAX_OUTPUT_CHARS", 250)
    msgs = [
        _Msg(tool_calls=[_tc("a0", "read_file", "{}")]),
        _Msg(tool_calls=[_tc("a1", "read_file", "{}")]),
        _Msg(tool_calls=[_tc("a2", "read_file", "{}")]),
        _Msg(content="forced synthesis (size)"),
    ]
    tools = [_big_tool("read_file", 100)]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = asyncio.run(Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    ).run("read everything", "chat"))

    assert reply == "forced synthesis (size)"
    assert reg.llm_client.chat.completions.calls == 4
    assert reg.llm_client.chat.completions.calls < AGENTIC_MAX_ITERATIONS + 1


def test_size_cap_does_not_fire_for_small_outputs() -> None:
    """Default cap: a normal small-output tool round is unaffected -- proves
    no regression vs the existing iteration-only backstop."""
    msgs = [
        _Msg(tool_calls=[_tc("a1", "shell_run", "{}")]),
        _Msg(content="done"),
    ]
    tools = [_big_tool("shell_run", 10)]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = asyncio.run(Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    ).run("count", "chat"))

    assert reply == "done"
    assert reg.llm_client.chat.completions.calls == 2
