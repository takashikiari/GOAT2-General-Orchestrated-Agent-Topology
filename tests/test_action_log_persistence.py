"""Tests for BUG-?: action log persists even when _history is None.

The previous _dispatch had the action-log persistence wrapped
inside ``store_and_promote``, which was only called inside
``if self._history is not None``. In production flows _history
is bootstrapped, so the bug was latent — but in any future
code path that calls _dispatch directly (tests, alternative
entry points) the action log was silently dropped.

The fix: extract a thin ``_persist_action_log`` method on the
supervisor and call it BEFORE the history check, so the action
log is always written when a turn result is available.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_action_log_persists_when_history_is_none():
    """When _history is None, _dispatch must still persist the
    action log so the next turn sees what tools ran."""
    from config.registry import ServiceRegistry
    from supervisor.supervisor import GoatSupervisor

    async def _run():
        sv = GoatSupervisor(ServiceRegistry())
        assert sv._history is None  # precondition for the test

        mm = MagicMock()
        mm.store = AsyncMock()
        sv.memory_manager = mm

        sv._last_turn_result = SimpleNamespace(
            called_tools=("memory_delete",) * 3,
            tool_results=("Key not found: X",) * 3,
            action="clarify",
            response="",
            clarification="[Reached the 6-tool per-turn limit while answering. Stopped here.]",
            source="generated",
        )
        # _dispatch must NOT crash even when _history is None, AND
        # must call mm.store for the action log.
        await sv._dispatch(intent="delete X", t0=0.0, turn=sv._last_turn_result)

        # Verify the action log was written: mm.store must have
        # been called with a key ending in ':actions'.
        store_calls = [c for c in mm.store.call_args_list]
        action_log_calls = [
            c for c in store_calls
            if c.args[1].endswith(":actions")
        ]
        assert action_log_calls, (
            f"action log was NOT persisted when _history is None. "
            f"mm.store calls: {[(c.args[0], c.args[1]) for c in store_calls]}"
        )
        # The payload is valid JSON containing one entry per call.
        import json as _json
        payload = action_log_calls[0].args[2]
        parsed = _json.loads(payload)
        assert len(parsed) == 3
        assert all(e["tool"] == "memory_delete" for e in parsed)
        assert all(e["ok"] is False for e in parsed)
    asyncio.run(_run())


def test_action_log_persists_when_history_is_present():
    """The existing happy-path still works — history + action log
    both persisted on a normal turn."""
    from config.registry import ServiceRegistry
    from supervisor.supervisor import GoatSupervisor
    from supervisor.session.history import ConversationHistory

    async def _run():
        sv = GoatSupervisor(ServiceRegistry())
        sv._history = ConversationHistory()
        sv._history.add_user("test", pending=True)

        mm = MagicMock()
        mm.store = AsyncMock()
        sv.memory_manager = mm

        sv._last_turn_result = SimpleNamespace(
            called_tools=("shell_run",),
            tool_results=("hello\n",),
            action="direct",
            response="hello",
            clarification="",
            source="generated",
        )
        await sv._dispatch(intent="test", t0=0.0, turn=sv._last_turn_result)

        # Both action log AND the standard intent/summary records
        # must be written.
        keys = [c.args[1] for c in mm.store.call_args_list]
        assert any(k.endswith(":actions") for k in keys), (
            f"action log missing among keys: {keys}"
        )
        assert any(k.endswith(":intent") for k in keys), (
            f"intent record missing among keys: {keys}"
        )
        assert any(k.endswith(":summary") for k in keys), (
            f"summary record missing among keys: {keys}"
        )
    asyncio.run(_run())


def test_dispatch_handles_action_log_persist_failure_gracefully():
    """A failure in the action-log persist must NOT crash the
    turn — the kernel-must-always-respond rule still holds."""
    from config.registry import ServiceRegistry
    from supervisor.supervisor import GoatSupervisor

    async def _run():
        sv = GoatSupervisor(ServiceRegistry())
        mm = MagicMock()
        mm.store = AsyncMock(side_effect=RuntimeError("redis down"))
        sv.memory_manager = mm

        sv._last_turn_result = SimpleNamespace(
            called_tools=("x",), tool_results=("y",),
            action="direct", response="ok", clarification="", source="generated",
        )
        # Must not raise.
        result = await sv._dispatch(intent="t", t0=0.0, turn=sv._last_turn_result)
        assert result.summary == "ok"
    asyncio.run(_run())