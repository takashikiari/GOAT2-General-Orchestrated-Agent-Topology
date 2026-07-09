"""tests.test_tool_result_truncation — _call_tool hard-truncates any single
tool result to TOOL_ROUND_MAX_OUTPUT_CHARS.

Root cause (2026-07-09 incident, part 2): the cumulative-output cap added to
_run_tool_round only decides whether ANOTHER round runs -- it can't undo a
result already folded into loop_msgs. get_recent_logs caps by line COUNT
(500 lines), not chars; a single call whose lines are long (e.g. this
benchmark's own verbose observability JSON) can return content many times
larger than the whole round budget, and that one oversized result is sent to
the API on the very next call regardless of the cumulative check. Truncating
centrally in _call_tool means no individual plugin has to get this right on
its own -- defense in depth, same shape as read_file/shell_run's own caps.
"""
from __future__ import annotations

import asyncio

from orchestrator import orchestrator as orchestrator_module
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient
from tests.test_agentic_loop import _tc
from tests.test_tool_round_output_cap import _Reg, _big_tool


def test_call_tool_truncates_oversized_single_result(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_module, "TOOL_ROUND_MAX_OUTPUT_CHARS", 50)
    tools = [_big_tool("get_recent_logs", 500)]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _LLMClient(_Completions("x")), _FakeAnalytics())
    orch = Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    )

    result = asyncio.run(orch._call_tool(_tc("a1", "get_recent_logs"), "chat", tools))

    assert result.startswith("x" * 50)
    assert "truncated" in result
    assert len(result) < 500


def test_call_tool_leaves_small_result_untouched() -> None:
    tools = [_big_tool("shell_run", 10)]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _LLMClient(_Completions("x")), _FakeAnalytics())
    orch = Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    )

    result = asyncio.run(orch._call_tool(_tc("a1", "shell_run"), "chat", tools))

    assert result == "x" * 10
