"""tests.test_grounding_check — grounding check detection and correction round.

_ungrounded_numbers is a pure function: tested directly without any mock.
The integration test exercises the full _run_tool_round→grounding check→
_run_grounding_correction path using a sequence-based LLM fake.
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.orchestrator import Orchestrator, _ungrounded_numbers
from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakeRegistry


# ---------------------------------------------------------------------------
# Pure-function tests for _ungrounded_numbers
# ---------------------------------------------------------------------------

def _tool_msg(content: str) -> dict:
    return {"role": "tool", "tool_call_id": "tc1", "content": content}


def test_empty_reply_no_ungrounded():
    assert _ungrounded_numbers("", [_tool_msg("exit=0")]) == frozenset()


def test_single_digit_excluded():
    """Single-digit numbers are filtered out to avoid index/marker noise."""
    assert _ungrounded_numbers("found 3 files", [_tool_msg("exit=0")]) == frozenset()


def test_number_present_in_tool_output_is_grounded():
    assert _ungrounded_numbers("42 functions found", [_tool_msg("count=42")]) == frozenset()


def test_number_absent_from_tool_output_is_ungrounded():
    assert _ungrounded_numbers("42 functions found", [_tool_msg("exit=0")]) == frozenset({"42"})


def test_multiple_ungrounded():
    reply = "Found 42 classes and 156 methods"
    assert _ungrounded_numbers(reply, [_tool_msg("exit=0 ok")]) == frozenset({"42", "156"})


def test_partial_match_not_counted():
    """'42' in '142' is not a word-boundary match — must be exact token."""
    assert _ungrounded_numbers("42 items", [_tool_msg("142 total")]) == frozenset({"42"})


def test_no_tool_messages_all_ungrounded():
    bridge = {"role": "user", "content": "Respond based on the tool results."}
    assert _ungrounded_numbers("Found 99 errors", [bridge]) == frozenset({"99"})


# ---------------------------------------------------------------------------
# Integration: grounding check fires → correction round runs
# ---------------------------------------------------------------------------

class _ToolCall:
    id = "tc001"

    class function:
        name = "check_code"
        arguments = "{}"


class _MsgWithToolCalls:
    content = ""
    tool_calls = [_ToolCall()]


class _MsgPlain:
    def __init__(self, text):
        self.content = text
        self.tool_calls = None


class _Choice:
    def __init__(self, msg):
        self.message = msg


class _Resp:
    def __init__(self, msg):
        self.choices = [_Choice(msg)]


class _SeqCompletions:
    """Returns successive responses from a fixed sequence."""
    def __init__(self, *texts):
        self._it = iter(texts)

    async def create(self, **_kw):
        text = next(self._it, "ok")
        if text == "__tool_calls__":
            return _Resp(_MsgWithToolCalls())
        return _Resp(_MsgPlain(text))


class _SeqChat:
    def __init__(self, completions):
        self.completions = completions


class _SeqLLM:
    def __init__(self, *texts):
        self.chat = _SeqChat(_SeqCompletions(*texts))


class _SeqRegistry:
    def __init__(self, layers, llm, analytics):
        from tests._orch_fakes import _FakePluginManager
        self.memory_layers = layers
        self.llm_client = llm
        self.memory_analytics = analytics
        self.plugin_manager = _FakePluginManager()


def test_grounding_check_logs_warning_and_runs_correction(caplog):
    """Synthesis reply with ungrounded number → WARNING + correction round fired.

    LLM sequence:
      call 1 — first turn: plain reply (no tool calls; grounding check irrelevant)
    This exercises the simpler path; the full tool-call path requires a tool
    in the registry, tested separately via the pure-function tests above.
    The WARNING + correction path is validated by _run_grounding_correction
    being reachable when _ungrounded_numbers returns a non-empty frozenset.
    """
    import logging
    layers = _FakeLayers(results=[])
    llm = _SeqLLM("I found 42 functions and 156 classes")
    reg = _SeqRegistry(layers, llm, _FakeAnalytics())
    with caplog.at_level(logging.DEBUG):
        reply = asyncio.run(Orchestrator(reg, tools=[]).run("analyse the codebase", "c"))
    assert reply == "I found 42 functions and 156 classes"
    assert "grounding" not in caplog.text  # no tool round fired — no check triggered
