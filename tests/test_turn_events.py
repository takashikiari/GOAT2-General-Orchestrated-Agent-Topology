"""tests.test_turn_events — optional per-turn listener for live tool activity (used by the admin panel chat)."""
from __future__ import annotations

import asyncio

from orchestrator import turn_events
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _LLMClient
from tests.test_agentic_loop import _tc
from tests.test_tool_round_output_cap import _Reg, _big_tool


def test_emit_without_a_listener_is_a_noop():
    turn_events.emit({"type": "tool_start"})


def test_bound_listener_receives_events_until_reset():
    seen: list[dict] = []
    token = turn_events.bind(seen.append)
    turn_events.emit({"type": "a"})
    turn_events.reset(token)
    turn_events.emit({"type": "b"})
    assert seen == [{"type": "a"}]


def test_a_failing_listener_never_breaks_the_turn():
    def boom(_event):
        raise RuntimeError("listener down")
    token = turn_events.bind(boom)
    try:
        turn_events.emit({"type": "a"})
    finally:
        turn_events.reset(token)


def _orch(tools):
    reg = _Reg(_FakeLayers(results=[]), _LLMClient(_Completions("x")), _FakeAnalytics())
    return Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client,
                        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools)


def test_call_tool_emits_start_and_end_with_the_call_id():
    tools = [_big_tool("echo_tool", 5)]
    orch = _orch(tools)
    seen: list[dict] = []
    token = turn_events.bind(seen.append)
    try:
        result = asyncio.run(orch._call_tool(_tc("call-1", "echo_tool"), "chat", tools))
    finally:
        turn_events.reset(token)
    assert [e["type"] for e in seen] == ["tool_start", "tool_end"]
    assert seen[0]["id"] == seen[1]["id"] == "call-1" and seen[0]["name"] == "echo_tool"
    assert seen[1]["result"] == result


def test_call_tool_unknown_tool_still_emits_an_end_event():
    orch = _orch([])
    seen: list[dict] = []
    token = turn_events.bind(seen.append)
    try:
        asyncio.run(orch._call_tool(_tc("call-2", "nope"), "chat", []))
    finally:
        turn_events.reset(token)
    assert [e["type"] for e in seen] == ["tool_start", "tool_end"] and "unknown tool" in seen[1]["result"]
