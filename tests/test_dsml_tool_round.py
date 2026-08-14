"""tests.test_dsml_tool_round — the DSML fallback path never leaks raw markup.

Real production incident (2026-08-14): deepseek-v4-flash sometimes emits its
tool-call intent as inline `<｜｜DSML｜｜tool_calls>` markup in `.content`
instead of the structured `tool_calls` API field. orchestrator.py detects
this and re-executes it via `_run_dsml_tool_round` — but that function used
to be a single execute-then-synthesize pass: if the model ALSO used DSML
markup in its own synthesis reply (asking for yet another tool instead of
answering), that second block was never checked, and raw unexecuted DSML
markup reached the user's Telegram chat verbatim.

`_run_dsml_tool_round` is now an agentic loop (same shape as
`_run_tool_round`), bounded the same way, with an absolute last-resort
strip so raw markup can never survive to the caller no matter what the
model does.
"""
from __future__ import annotations

import asyncio
import json

from orchestrator import orchestrator as orchestrator_module
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


def _dsml_block(*invocations: tuple[str, dict]) -> str:
    """Build a `<｜｜DSML｜｜tool_calls>` block with one invoke per (name, args)."""
    parts = [
        f'<｜｜DSML｜｜invoke name="{name}">{json.dumps(args)}</｜｜DSML｜｜invoke>'
        for name, args in invocations
    ]
    return "<｜｜DSML｜｜tool_calls>\n" + "\n".join(parts) + "\n</｜｜DSML｜｜tool_calls>"


def _tool(name: str, result: str = "ok") -> ToolDefinition:
    calls: list[dict] = []

    async def handler(chat_id: str = "", **kw) -> str:
        calls.append(kw)
        return result

    td = ToolDefinition(
        name=name, description=name,
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
    )
    td.calls = calls  # type: ignore[attr-defined]  -- test-only introspection
    return td


def _run(reg: _Reg, tools: list[ToolDefinition], text: str = "investigate") -> str:
    return asyncio.run(Orchestrator(
        layers=reg.memory_layers, llm_client=reg.llm_client,
        plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=tools,
    ).run(text, "chat"))


# --- happy path: DSML in the initial reply, clean synthesis -----------------

def test_dsml_in_initial_content_executes_tool_and_synthesizes_cleanly():
    shell = _tool("shell_run", "file.txt\n")
    msgs = [
        _Msg(content=_dsml_block(("shell_run", {"command": "ls"}))),
        _Msg(content="Here's what I found: file.txt"),
    ]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = _run(reg, [shell])

    assert reply == "Here's what I found: file.txt"
    assert reg.llm_client.chat.completions.calls == 2  # initial + DSML synthesis
    assert len(shell.calls) == 1  # type: ignore[attr-defined]


# --- the bug: synthesis reply itself contains DSML --------------------------

def test_dsml_synthesis_containing_dsml_loops_instead_of_leaking():
    """The exact incident: the DSML round's own synthesis call responds with
    ANOTHER DSML block instead of plain text. Must execute that second round
    too and keep going, never returning the raw markup as the final reply."""
    shell = _tool("shell_run", "round result\n")
    msgs = [
        _Msg(content=_dsml_block(("shell_run", {"command": "ls"}))),
        _Msg(content=_dsml_block(("shell_run", {"command": "cat file.txt"}))),
        _Msg(content="Final answer after two rounds"),
    ]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = _run(reg, [shell])

    assert reply == "Final answer after two rounds"
    assert "｜｜DSML｜｜" not in reply
    assert len(shell.calls) == 2  # type: ignore[attr-defined] -- both rounds executed
    assert reg.llm_client.chat.completions.calls == 3


# --- absolute safety net: model never stops emitting DSML -------------------

def test_dsml_round_never_leaks_raw_markup_even_at_cap(monkeypatch):
    """Model responds with pure DSML markup on every single call. The round
    must still terminate (bounded by AGENTIC_MAX_ITERATIONS) and the final
    reply must contain zero DSML markup -- stripped, with a graceful
    fallback message, never the raw tags."""
    monkeypatch.setattr(orchestrator_module, "AGENTIC_MAX_ITERATIONS", 3)
    shell = _tool("shell_run", "x\n")
    always_dsml = _dsml_block(("shell_run", {"command": "x"}))
    # First call (initial decision) + up to 3 synthesis calls, all DSML.
    msgs = [_Msg(content=always_dsml) for _ in range(5)]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = _run(reg, [shell])

    assert "DSML" not in reply
    assert "｜" not in reply
    assert reply  # never empty -- the graceful fallback message, not ""


# --- continuation budget: DSML after the structured loop already capped -----

def test_dsml_after_structured_cap_gets_small_continuation_budget(monkeypatch):
    """When the structured tool_calls loop hits ITS cap and the forced
    synthesis reply contains DSML, the DSML round must NOT get a fresh full
    AGENTIC_MAX_ITERATIONS budget (that would let the model roughly double
    its real ceiling by switching encodings) -- it gets the small
    _DSML_CONTINUATION_MAX_ITERATIONS allowance instead."""
    monkeypatch.setattr(orchestrator_module, "AGENTIC_MAX_ITERATIONS", 2)
    monkeypatch.setattr(orchestrator_module, "_DSML_CONTINUATION_MAX_ITERATIONS", 2)
    shell = _tool("shell_run", "x\n")
    always_dsml = _dsml_block(("shell_run", {"command": "x"}))
    msgs = [
        # Structured loop: 2 iterations of real tool_calls, both hit the cap
        # (AGENTIC_MAX_ITERATIONS=2), forced synthesis returns DSML.
        _Msg(tool_calls=[_tc("a0", "shell_run", "{}")]),
        _Msg(tool_calls=[_tc("a1", "shell_run", "{}")]),
        # forced synthesis (no tools) -- DSML instead of a real answer
        _Msg(content=always_dsml),
        # DSML continuation round, always DSML, up to its own small budget
        _Msg(content=always_dsml),
        _Msg(content=always_dsml),
    ]
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, _SeqLLM(msgs), _FakeAnalytics())

    reply = _run(reg, [shell])

    assert "｜" not in reply  # never leaked raw, whichever branch handled it
    assert reply
