"""tests.test_conversation_runner_tool_evidence — _run_one_turn passes the
turn's tool-call evidence to Evaluator.groundedness_judge, so tool-grounded
answers aren't misjudged as hallucinations just because they're absent from
the memory context blocks (see test_orchestrator_tool_summary_callback.py
and test_evaluator_groundedness.py for the two halves of this wiring).
"""
from __future__ import annotations

import asyncio
import json

from benchmark.conversation_runner import _run_one_turn
from memory.analytics import MemoryAnalytics
from orchestrator.orchestrator import Orchestrator
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _FakeLayers, _FakeRegistry
from tests.test_agentic_loop import _Msg, _tc


class _Resp:
    def __init__(self, msg) -> None:
        self.choices = [type("C", (), {"message": msg})()]


class _CapturingSeqCompletions:
    def __init__(self, messages) -> None:
        self._msgs = list(messages)
        self.calls = 0
        self.all_messages: list[list[dict]] = []

    async def create(self, **kw):
        self.all_messages.append(kw.get("messages"))
        self.calls += 1
        i = min(self.calls - 1, len(self._msgs) - 1)
        return _Resp(self._msgs[i])


class _Chat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _LLM:
    def __init__(self, completions) -> None:
        self.chat = _Chat(completions)


def _tool(name: str, result: str) -> ToolDefinition:
    async def handler(chat_id: str = "", **_kw) -> str:
        return result

    return ToolDefinition(
        name=name, description=name,
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
    )


def test_tool_evidence_reaches_groundedness_judge_prompt() -> None:
    judge_reply = json.dumps({"grounded": True, "hallucinated_claims": [], "answered_without_evidence": False})
    msgs = [
        _Msg(tool_calls=[_tc("a1", "shell_run", '{"command":"grep -n log memory/layers.py"}')]),
        _Msg(content="4 log lines found."),
        _Msg(content=judge_reply),
    ]
    completions = _CapturingSeqCompletions(msgs)
    llm = _LLM(completions)
    layers = _FakeLayers(results=[])
    registry = _FakeRegistry(layers, llm, MemoryAnalytics())
    orch = Orchestrator(
        layers=registry.memory_layers, llm_client=registry.llm_client,
        plugin_manager=registry.plugin_manager, analytics=registry.memory_analytics,
        tools=[_tool("shell_run", "87: log.warning(...)\n328: log.debug(...)")],
    )

    asyncio.run(_run_one_turn(orch, registry, "chat1", "how many logs?"))

    judge_call_messages = completions.all_messages[-1]
    judge_user_msg = judge_call_messages[1]["content"]
    assert "shell_run" in judge_user_msg
    assert "log.warning" in judge_user_msg
