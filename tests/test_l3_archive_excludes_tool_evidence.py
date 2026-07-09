"""tests.test_l3_archive_excludes_tool_evidence — L3 archive gets the clean
synthesized reply only; L2 working context keeps the full [Tool calls]
evidence block.

Root cause (2026-07-09 investigation): _archive_turn was called with
saved_reply (the same string saved to L2, including raw tool output/paths/
JSON previews via [Tool calls]), so every tool-using turn permanently wrote
that raw evidence into L3 -- prod measurement found the longest, least-
enriched L3 entries were almost all raw tool dumps (find/ls/get_recent_logs
output). L2 still needs the full evidence for in-session grounding (see
_archive_turn's original comment: without it, a past tool claim would be
indistinguishable from a hallucination under questioning THIS session) --
only the L3 permanent archive changes. Trade-off: a NEW chat_id recalling
"did you check X" no longer finds tool proof in L3, only the synthesized
claim -- accepted deliberately to stop raw tool dumps from polluting the
permanent corpus.
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


def test_l3_archive_content_has_no_tool_calls_block() -> None:
    msgs = [
        _Msg(tool_calls=[_tc("a1", "shell_run", '{"command":"find / -name *.py"}')]),
        _Msg(content="Found 12 files."),
    ]
    tools = [_tool("shell_run", "/a.py\n/b.py\n... raw find output ...")]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())
    orch = Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    )

    reply = asyncio.run(orch.run("how many py files", "chat"))

    assert reply == "Found 12 files."
    archived = getattr(layers, "archived_contents", [])
    assert len(archived) >= 1
    assert "[Tool calls]" not in archived[0]
    assert "raw find output" not in archived[0]
    assert "Found 12 files." in archived[0]


def test_l2_working_context_still_has_full_tool_calls_block() -> None:
    """Same turn as above: L2 must keep the full evidence for in-session
    grounding -- only L3 changes."""
    msgs = [
        _Msg(tool_calls=[_tc("a1", "shell_run", '{"command":"find / -name *.py"}')]),
        _Msg(content="Found 12 files."),
    ]
    tools = [_tool("shell_run", "/a.py\n/b.py\n... raw find output ...")]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())
    orch = Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    )

    asyncio.run(orch.run("how many py files", "chat"))

    saved_assistant = [m for m in layers.saved if m["role"] == "assistant"][0]["content"]
    assert "[Tool calls]" in saved_assistant
    assert "raw find output" in saved_assistant


def test_l3_archive_content_equals_reply_when_no_tools_used() -> None:
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM([_Msg(content="plain answer")]), _FakeAnalytics())
    orch = Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[],
    )

    asyncio.run(orch.run("hi", "chat"))

    archived = getattr(layers, "archived_contents", [])
    assert archived[0] == "user: hi\nassistant: plain answer"
