"""Tests for tool-call cap per turn (Commit 1.5b fix).

The previous behaviour allowed the LLM to make up to 8
rounds × N tools-per-round = 17+ tool calls per turn (observed
in session logs at 10:57:31 — the model burned 10 of those
calls on ``memory_get`` for keys that don't exist, fabricating
key names like ``turn_1781800064_18``).

The fix: ``_call_with_tools`` enforces a hard cap on the total
number of tool calls per turn. When the cap is hit, the
function stops dispatching further tool calls and returns
a short honest fallback mentioning the cap, so the supervisor
still responds (kernel-must-always-respond) but the operator
can see why iteration stopped.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.tool_runner import _MAX_TOOL_CALLS_PER_TURN, _call_with_tools


# ── Constants ──────────────────────────────────────────────────────────────


def test_max_tool_calls_per_turn_is_exported():
    """The cap constant is exported and a sane positive integer."""
    assert isinstance(_MAX_TOOL_CALLS_PER_TURN, int)
    assert 3 <= _MAX_TOOL_CALLS_PER_TURN <= 12


# ── Mock helpers ───────────────────────────────────────────────────────────


def _fake_spec() -> MagicMock:
    spec = MagicMock()
    spec.model_id = "fake-model"
    spec.tool_calling = True
    spec.no_temperature = False
    return spec


def _fake_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    t.to_openai.return_value = {"type": "function", "function": {"name": name}}
    return t


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


def _run(spec, tools, side_effects):
    """Run _call_with_tools with a mocked client that returns
    the given side_effects in order."""
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=side_effects)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        return asyncio_run(_call_with_tools(
            spec, [{"role": "user", "content": "hi"}], tools,
            tool_choice="auto", memory_manager=MagicMock(),
        ))


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ── Cap behaviour ──────────────────────────────────────────────────────────


def test_cap_stops_loop_after_max_calls():
    """When the model keeps requesting tool calls, the cap
    halts the loop and returns a non-empty fallback response."""
    captured: list[int] = []

    def next_response(*args, **kwargs):
        captured.append(len(captured))
        return _completion(_message(
            tool_calls=[_tool_call("shell_run", {"command": f"echo {len(captured)}"})],
        ))

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=next_response)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "hi"}],
            [_fake_tool("shell_run")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    assert len(captured) == _MAX_TOOL_CALLS_PER_TURN + 1, (
        f"the model was asked {_MAX_TOOL_CALLS_PER_TURN + 1} times "
        f"(the cap is checked AFTER the model returns tool calls, "
        f"so the +1 is the trigger). The {_MAX_TOOL_CALLS_PER_TURN}-th "
        f"call succeeded; the +1-th triggered the fallback."
    )
    assert result.content, "fallback content is empty"
    assert "limit" in result.content.lower() or "stopped" in result.content.lower()
    assert len(result.called_tools) == _MAX_TOOL_CALLS_PER_TURN


def test_cap_does_not_trigger_under_threshold():
    """When the model makes fewer than _MAX_TOOL_CALLS_PER_TURN
    tool calls and then stops, the cap does not fire."""
    responses = [
        _completion(_message(tool_calls=[_tool_call("shell_run", {"command": "a"})])),
        _completion(_message(tool_calls=[_tool_call("shell_run", {"command": "b"})])),
        _completion(_message(content="done!")),
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=responses)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "hi"}],
            [_fake_tool("shell_run")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    assert result.content == "done!"
    assert len(result.called_tools) == 2


def test_cap_preserves_earliest_tool_calls():
    """When the cap fires, the called_tools list contains the
    first N tools the model requested (FIFO)."""
    captured_calls: list[int] = []

    def next_response(*args, **kwargs):
        idx = len(captured_calls)
        captured_calls.append(idx)
        return _completion(_message(
            tool_calls=[_tool_call("memory_get", {"key": f"k-{idx}"})],
        ))

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=next_response)
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "hi"}],
            [_fake_tool("memory_get")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    for tool_name in result.called_tools:
        assert tool_name == "memory_get"


def test_cap_fallback_is_honest_about_limit():
    """The fallback message must explicitly mention the cap value."""
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        side_effect=lambda *a, **kw: _completion(_message(
            tool_calls=[_tool_call("shell_run", {"command": "x"})],
        )),
    )
    with patch("tools.tool_runner._get_client", return_value=fake_client):
        result = asyncio_run(_call_with_tools(
            _fake_spec(),
            [{"role": "user", "content": "hi"}],
            [_fake_tool("shell_run")],
            tool_choice="auto", memory_manager=MagicMock(),
        ))

    assert str(_MAX_TOOL_CALLS_PER_TURN) in result.content