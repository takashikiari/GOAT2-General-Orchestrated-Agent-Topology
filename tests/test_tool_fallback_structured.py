"""Tests for the structured fallback when the LLM returns empty content after tool calls.

Background: the empty-content branch at tools/tool_runner.py:226-231 used to
return the LAST tool's raw result as the user-facing reply. In production,
that meant a ``memory_get`` returning ``"Key not found"`` was presented as
GOAT's answer — and on the next turn the model confabulated multi-step
stories from that single tool result (see Commit 1.5 root-cause note).

The fix: when the model returns no visible content after tool calls, format
ALL tool results as a structured block (``Tool X returned: Y``) so the user
sees something honest that cannot be confused with a model claim. This
keeps the change local — no extra LLM call, no behavioural surprise.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.tool_runner import _call_with_tools


# ── Helpers (mirror test_tool_call_cap.py style) ───────────────────────────


def _fake_spec() -> MagicMock:
    spec = MagicMock()
    spec.model_id = "fake-model"
    spec.tool_calling = True
    spec.no_temperature = False
    return spec


def _fake_tool(name: str) -> MagicMock:
    """A fake ToolDefinition. The handler returns a fixed string when called.

    We attach a real ``parameters`` dict (empty schema) so ``_prepare_args``
    is happy, and an async ``handler`` that returns ``return_value``.
    """
    t = MagicMock()
    t.name = name
    t.to_openai.return_value = {"type": "function", "function": {"name": name}}
    t.parameters = {"type": "object", "properties": {}, "required": []}

    # ``handler`` is set per-test (closure over return_value).
    async def _h(**_kwargs):
        return _HANDLER_RETURN_VALUE

    t.handler = _h
    return t


# Module-level so the async handler closure above can read it.
_HANDLER_RETURN_VALUE: str = ""


def _tool_call(name: str, args: dict, call_id: str = "1") -> MagicMock:
    tc = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    tc.id = call_id
    return tc


def _completion(message: MagicMock) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = message
    return resp


def _message(content: str = "", tool_calls: list | None = None) -> MagicMock:
    m = MagicMock()
    m.content = content
    m.tool_calls = tool_calls or []
    return m


def asyncio_run(coro):
    return asyncio.run(coro)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_single_tool_empty_model_content_gets_structured_fallback():
    """When the model calls one tool and returns no content, the
    fallback must be a structured ``Tool X returned: Y`` block —
    NOT the raw tool result."""
    global _HANDLER_RETURN_VALUE
    _HANDLER_RETURN_VALUE = "Key not found"

    responses = [
        _completion(_message(tool_calls=[_tool_call("memory_get", {"key": "x"})])),
        _completion(_message(content="")),  # model goes silent
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=responses)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "lookup x"}],
            [_fake_tool("memory_get")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    # The fallback must mention the tool name so it cannot be confused
    # with a free-text model claim.
    assert "memory_get" in result.content
    assert "Key not found" in result.content
    # And it must be structured, not just the raw result.
    # The buggy implementation returned exactly "Key not found" as the
    # whole reply. A structured fallback uses "Tool X returned: Y" form.
    assert result.content != "Key not found", (
        "fallback returned the raw tool result as-is — the model can "
        "confabulate multi-step claims from this on the next turn. "
        "The fix wraps it as 'Tool memory_get returned: Key not found'."
    )


def test_multiple_tools_empty_content_aggregates_all_results():
    """When the model calls 3 tools and returns no content, the
    fallback must show ALL 3 results — not just the last one."""
    call_log: list[str] = []

    def make_handler(name: str, ret: str):
        async def _h(**_kwargs):
            call_log.append(name)
            return ret
        return _h

    tool_a = _fake_tool("memory_get")
    tool_a.handler = make_handler("memory_get", "Value-A")
    tool_b = _fake_tool("memory_search")
    tool_b.handler = make_handler("memory_search", "Value-B")
    tool_c = _fake_tool("shell_run")
    tool_c.handler = make_handler("shell_run", "Value-C")

    # Model calls all 3 in one round, then goes silent.
    responses = [
        _completion(_message(tool_calls=[
            _tool_call("memory_get", {"key": "a"}, call_id="a1"),
            _tool_call("memory_search", {"q": "b"}, call_id="b1"),
            _tool_call("shell_run", {"command": "c"}, call_id="c1"),
        ])),
        _completion(_message(content="")),
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=responses)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "do stuff"}],
            [tool_a, tool_b, tool_c],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    # All three tool names and results must appear.
    for name in ("memory_get", "memory_search", "shell_run"):
        assert name in result.content, f"tool {name!r} missing from fallback"
    for value in ("Value-A", "Value-B", "Value-C"):
        assert value in result.content, f"tool result {value!r} missing from fallback"
    # And the fallback must be clearly multi-tool, not just the last result.
    assert "Value-C" in result.content  # last
    assert "Value-A" in result.content  # first — would be MISSING under the buggy impl


def test_model_content_nonempty_does_not_trigger_fallback():
    """When the model DOES emit visible content, the fallback is
    bypassed entirely — the model's own text is the answer."""
    responses = [
        _completion(_message(tool_calls=[_tool_call("memory_get", {"key": "x"})])),
        _completion(_message(content="The value is 42.")),
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=responses)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "lookup x"}],
            [_fake_tool("memory_get")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    assert result.content == "The value is 42."
    # The fallback template must NOT appear in the model's own reply.
    assert "Tool " not in result.content


def test_no_tools_called_empty_content_falls_back_to_empty():
    """When the model returns empty content without calling any tools,
    the fallback is empty (no tools to aggregate). The function still
    returns without crashing."""
    responses = [_completion(_message(content=""))]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=responses)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "say nothing"}],
            [_fake_tool("memory_get")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    # No tools were called and content was empty → no fallback content
    # to fabricate. The function should return without raising.
    assert result.called_tools == ()
    # The content may be empty string — that's the existing behaviour
    # for the no-tools-called case.


def test_fallback_truncates_long_tool_results():
    """A tool result longer than the configured cap is truncated,
    so a single huge result cannot blow up the user-facing reply."""
    global _HANDLER_RETURN_VALUE
    _HANDLER_RETURN_VALUE = "X" * 5000  # 5000-char result

    responses = [
        _completion(_message(tool_calls=[_tool_call("memory_get", {"key": "x"})])),
        _completion(_message(content="")),
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=responses)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "lookup x"}],
            [_fake_tool("memory_get")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    # The fallback must be SHORTER than the raw 5000-char result —
    # otherwise we have not fixed anything.
    assert len(result.content) < 1000, (
        f"fallback length {len(result.content)} not truncated — "
        f"would still produce a 5000-char reply to the user"
    )
    # And it must still mention the tool name so it's clearly a tool result.
    assert "memory_get" in result.content
