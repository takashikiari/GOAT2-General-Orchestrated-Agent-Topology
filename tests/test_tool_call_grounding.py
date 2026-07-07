"""tests.test_tool_call_grounding — tool-call evidence persisted into L2/L3.

Regression for: when a turn uses tools, only the synthesized reply was saved
to L2/L3, leaving future turns with no real grounding for "did you actually
call X?" — the prior narrative text was indistinguishable from a hallucination.

Fix: _run_tool_round now returns (reply, tool_summary); run() embeds the summary
in the saved assistant message as a [Tool calls] block before the synthesis text.
"""
from __future__ import annotations

import asyncio

from memory.context_assembler import format_messages
from orchestrator.orchestrator import Orchestrator
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakePluginManager


# ---------------------------------------------------------------------------
# Fake LLM that returns tool_calls on call 1, plain text on call 2
# ---------------------------------------------------------------------------

class _ShellToolCall:
    id = "tc_shell_001"

    class function:
        name = "shell_run"
        arguments = '{"command":"wc -l *.py"}'


class _MsgWithShellRun:
    content = ""
    tool_calls = [_ShellToolCall()]


class _MsgPlain:
    def __init__(self, text: str) -> None:
        self.content = text
        self.tool_calls = None


class _Choice:
    def __init__(self, msg) -> None:
        self.message = msg


class _Resp:
    def __init__(self, msg) -> None:
        self.choices = [_Choice(msg)]


class _ToolSeqCompletions:
    """Call 1 → tool_calls (shell_run); call 2 → synthesis text; call 3+ → 'ok'."""
    def __init__(self, synthesis: str) -> None:
        self._synthesis = synthesis
        self._calls = 0

    async def create(self, **_kw):
        self._calls += 1
        if self._calls == 1:
            return _Resp(_MsgWithShellRun())
        if self._calls == 2:
            return _Resp(_MsgPlain(self._synthesis))
        return _Resp(_MsgPlain("ok"))


class _ToolSeqChat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _ToolSeqLLM:
    def __init__(self, synthesis: str) -> None:
        self.chat = _ToolSeqChat(_ToolSeqCompletions(synthesis))


class _ToolSeqRegistry:
    def __init__(self, layers, llm, analytics) -> None:
        self.memory_layers = layers
        self.llm_client = llm
        self.memory_analytics = analytics
        self.plugin_manager = _FakePluginManager()


# ---------------------------------------------------------------------------
# Helper: fake shell_run tool
# ---------------------------------------------------------------------------

def _make_shell_tool(result: str) -> ToolDefinition:
    async def handler(command: str, timeout: int = 30, chat_id: str = "") -> str:
        return result

    return ToolDefinition(
        name="shell_run",
        description="run shell command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_tool_call_turn_saves_compact_summary_in_l2():
    """After a tool-call turn, L2 saved assistant message contains [Tool calls] evidence.

    Regression: before the fix, only the synthesis text was saved. A later
    turn asking "did you actually run that?" had only the model's own prior
    claim as evidence — identical in structure to a fabricated assertion.
    """
    synthesis = "The file counts are in."
    tool = _make_shell_tool("6\n2\n")

    layers = _FakeLayers(results=[])
    reg = _ToolSeqRegistry(layers, _ToolSeqLLM(synthesis), _FakeAnalytics())
    asyncio.run(Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[tool]).run("count lines in py files", "chat1"))

    saved = layers.saved
    assistant_entries = [m for m in saved if m["role"] == "assistant"]
    assert len(assistant_entries) == 1, "expected exactly one saved assistant message"
    content = assistant_entries[0]["content"]

    assert "[Tool calls]" in content, "evidence block missing from saved L2 message"
    assert "shell_run" in content, "tool name missing from evidence"
    assert "wc -l" in content, "command args missing from evidence"
    assert "6" in content, "tool result value missing from evidence"

    # Synthesis reply is present and comes after the evidence block
    assert synthesis in content, "synthesis reply missing from saved message"
    assert content.index("[Tool calls]") < content.index(synthesis), (
        "[Tool calls] block must precede synthesis text"
    )


def test_second_turn_context_contains_tool_evidence():
    """The [Tool calls] block in L2 is visible in _format_messages output for the next turn.

    Simulates what the second turn's [Conversation History] block would show —
    verifying the evidence survives the format_messages rendering that feeds
    the LLM prompt, so the model has real data to check against under questioning.
    """
    synthesis = "The file counts are in."
    tool = _make_shell_tool("6\n2\n")

    layers = _FakeLayers(results=[])
    reg = _ToolSeqRegistry(layers, _ToolSeqLLM(synthesis), _FakeAnalytics())
    asyncio.run(Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[tool]).run("count lines in py files", "chat1"))

    # Simulate what the next turn assembles as [Conversation History]
    history_block = format_messages(layers.saved)

    assert "[Tool calls]" in history_block, "evidence block missing from conversation history"
    assert "shell_run" in history_block, "tool name missing from conversation history"
    assert "6" in history_block, "tool result missing from conversation history"


def test_no_tool_call_turn_saves_plain_reply():
    """Turn with no tool calls saves the plain reply without a [Tool calls] prefix."""
    from tests._orch_fakes import _Completions, _LLMClient, _FakeRegistry

    layers = _FakeLayers(results=[])
    reg = _FakeRegistry(layers, _LLMClient(_Completions("just a plain reply")), _FakeAnalytics())
    asyncio.run(Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[]).run("hello", "chat2"))

    saved = layers.saved
    assistant_entries = [m for m in saved if m["role"] == "assistant"]
    assert len(assistant_entries) == 1
    content = assistant_entries[0]["content"]

    assert content == "just a plain reply", (
        f"plain turn should not get [Tool calls] prefix, got: {content!r}"
    )
    assert "[Tool calls]" not in content


def test_compact_tool_summary_short_result_stored_in_full():
    """Short results (≤ 400 chars) are stored verbatim regardless of tool type."""
    from orchestrator.orchestrator import _compact_tool_summary

    result = _compact_tool_summary([("shell_run", '{"command":"echo hi"}', "hi\n")])
    assert "hi\n" in result
    assert "shell_run" in result
    assert "echo hi" in result


def test_compact_tool_summary_large_output_tool_stores_length_only():
    """browse_page/fetch_content: only '[N chars]' stored, not page content."""
    from orchestrator.orchestrator import _compact_tool_summary

    big_page = "x" * 5000
    result = _compact_tool_summary([("browse_page", '{"url":"https://example.com"}', big_page)])
    assert "[5000 chars]" in result
    assert "x" * 10 not in result  # no page content in summary


def test_compact_tool_summary_shell_run_long_uses_head_and_tail():
    """Long shell_run output uses head+tail so end-of-output totals are captured."""
    from orchestrator.orchestrator import _compact_tool_summary, _RESULT_HEAD, _RESULT_TAIL

    # Craft output where the count total appears at the end (like wc -l)
    many_lines = "\n".join(f"   {i} file{i}.py" for i in range(1, 50))
    total_line = "  1225 total"
    long_output = many_lines + "\n" + total_line  # total is at the end

    result = _compact_tool_summary([("shell_run", '{"command":"wc -l *.py"}', long_output)])

    # Head must be present (first _RESULT_HEAD chars)
    assert long_output[:_RESULT_HEAD] in result, "head of output missing"
    # Tail must be present (last _RESULT_TAIL chars) — captures the total line
    assert long_output[-_RESULT_TAIL:] in result, "tail of output missing (totals line cut off)"
    # The '...' separator must be present
    assert "..." in result
