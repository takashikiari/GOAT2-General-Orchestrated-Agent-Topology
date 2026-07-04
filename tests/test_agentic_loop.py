"""tests.test_agentic_loop — the orchestrator's multi-iteration tool loop.

The tool round is an agentic loop: the model chains tools across one turn
(read -> search -> write -> verify -> synthesize) in whatever order it needs,
deciding its own next step, instead of one tool batch then forced synthesis.
Below ``AGENTIC_MAX_ITERATIONS`` the model is called WITH tools (it may chain
another tool or answer with text = natural termination); at the cap it is
called WITHOUT tools so a stuck model must synthesize from what it has.

These tests prove: (1) the loop chains multiple tools then synthesises, with
every tool call landing in the [Tool calls] evidence block; (2) a single tool
still terminates in 2 calls (no regression vs the old single-round path); (3)
the cap is a hard backstop — when the model never stops calling tools, the
cap withholds tools and forces synthesis, with no content inspection.
"""
from __future__ import annotations

import asyncio

from orchestrator.orchestrator import AGENTIC_MAX_ITERATIONS, Orchestrator
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakePluginManager


# --- fakes -----------------------------------------------------------------

class _ToolCall:
    def __init__(self, id_: str, name: str, args: str) -> None:
        self.id = id_
        self.function = type("f", (), {"name": name, "arguments": args})()


class _Msg:
    def __init__(self, content: str = "", tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, msg: _Msg) -> None:
        self.message = msg


class _Resp:
    def __init__(self, msg: _Msg) -> None:
        self.choices = [_Choice(msg)]


class _SeqCompletions:
    """Return a queued sequence of messages, one per ``create()`` call.

    Each message is a ``_Msg`` (with ``tool_calls`` to keep the loop going, or
    text to stop it). The final message is held for any calls past the queue
    end. ``calls`` counts ``create()`` invocations so tests can assert the LLM
    call count.
    """

    def __init__(self, messages: list[_Msg]) -> None:
        self._msgs = list(messages)
        self.calls = 0

    async def create(self, **_kw):
        self.calls += 1
        i = min(self.calls - 1, len(self._msgs) - 1)
        return _Resp(self._msgs[i])


class _SeqChat:
    def __init__(self, completions: _SeqCompletions) -> None:
        self.completions = completions


class _SeqLLM:
    def __init__(self, messages: list[_Msg]) -> None:
        self.chat = _SeqChat(_SeqCompletions(messages))


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


def _tc(id_: str, name: str, args: str = "{}") -> _ToolCall:
    return _ToolCall(id_, name, args)


def _saved_assistant(layers: _FakeLayers) -> str:
    return [m for m in layers.saved if m["role"] == "assistant"][0]["content"]


# --- natural termination: model chains 2 tools, then answers ----------------

def test_loop_chains_multiple_tools_then_synthesizes():
    """Two tool batches in one turn (shell_run then read_file), then a text reply.

    Proves the loop is agentic: the model chains a second tool after seeing the
    first result, instead of being forced to synthesise after one batch. Both
    tool calls land in the [Tool calls] evidence block saved to L2/L3.
    """
    msgs = [
        _Msg(tool_calls=[_tc("a1", "shell_run", '{"command":"ls"}')]),
        _Msg(tool_calls=[_tc("a2", "read_file", '{"path":"x.txt"}')]),
        _Msg(content="synthesis from both tools"),
    ]
    tools = [_tool("shell_run", "file.txt"), _tool("read_file", "file contents")]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = asyncio.run(Orchestrator(reg, tools=tools).run("do it", "chat"))

    assert reply == "synthesis from both tools"
    assert reg.llm_client.chat.completions.calls == 3  # init + 2 loop calls
    saved = _saved_assistant(layers)
    assert "[Tool calls]" in saved
    assert "shell_run" in saved and "read_file" in saved  # BOTH calls in evidence
    assert layers.archive_calls >= 1  # the turn still archives to L3


# --- single tool: no regression vs the old single-round path ----------------

def test_loop_single_tool_terminates_after_one_batch():
    """One tool then text: still 2 LLM calls (init + 1 loop), no extra calls."""
    msgs = [
        _Msg(tool_calls=[_tc("a1", "shell_run", '{"command":"wc -l"}')]),
        _Msg(content="done"),
    ]
    tools = [_tool("shell_run", "6\n")]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = asyncio.run(Orchestrator(reg, tools=tools).run("count", "chat"))

    assert reply == "done"
    assert reg.llm_client.chat.completions.calls == 2


# --- cap is a hard backstop (model never stops calling tools) --------------

def test_cap_forces_synthesis_when_model_keeps_calling_tools():
    """Model calls tools on every call -> cap withholds tools and forces synthesis.

    Proves the cap is a backstop, not a grounding decider: at the cap tools are
    withheld and the model synthesises from what it has gathered. No content is
    inspected, no claim withdrawn. Total LLM calls = AGENTIC_MAX_ITERATIONS + 1
    (the +1 is the initial decision call in ``run()``).
    """
    # calls 1..AGENTIC_MAX_ITERATIONS return tool_calls; the forced (no-tools)
    # call returns text.
    tool_msgs = [_Msg(tool_calls=[_tc(f"a{i}", "shell_run", "{}")])
                 for i in range(AGENTIC_MAX_ITERATIONS)]
    msgs = tool_msgs + [_Msg(content="forced synthesis")]
    tools = [_tool("shell_run", "out")]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = asyncio.run(Orchestrator(reg, tools=tools).run("loop forever", "chat"))

    assert reply == "forced synthesis"
    assert reg.llm_client.chat.completions.calls == AGENTIC_MAX_ITERATIONS + 1
    saved = _saved_assistant(layers)
    # every iteration's shell_run appears in the evidence block
    assert saved.count("shell_run") >= AGENTIC_MAX_ITERATIONS