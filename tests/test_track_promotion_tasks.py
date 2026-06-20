"""Tests for BUG-027 fix: track background promotion tasks.

The previous turn_persistence.schedule_promotion() spawned an
asyncio task with ``asyncio.create_task(...)`` but never awaited
it. The exceptions raised inside the task were silently lost,
and a fast ``GOAT.finalize_session()`` could exit before the
task finished — losing the promotion entirely.

The fix:
  - Supervisor keeps a registry of background tasks in
    ``_background_tasks: dict[str, asyncio.Task]``.
  - schedule_promotion adds the task to the registry keyed by
    a stable id ('turn-promotion:<n>').
  - The task body wraps the actual promotion call in a
    try/except that surfaces errors via the standard log
    channel at WARNING (not DEBUG — operators must see these).
  - ``await finalize_background_tasks()`` on supervisor shutdown
    drains pending tasks with a bounded timeout.
"""
from __future__ import annotations

import asyncio
import logging
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.registry import ServiceRegistry
from supervisor.session.turn_persistence import schedule_promotion
from supervisor.supervisor import GoatSupervisor


# ── Task creation ──────────────────────────────────────────────────────────


def test_schedule_promotion_registers_task_in_background_registry():
    """schedule_promotion must add its task to
    supervisor._background_tasks, not let it float unattached."""
    async def _run():
        sv = GoatSupervisor(ServiceRegistry())
        sv.memory_manager = mock.MagicMock()
        async def _noop():
            return None
        sv.memory_manager.promote_turns = _noop

        schedule_promotion(sv, turn_count=42)

        # The task must be tracked.
        assert len(sv._background_tasks) == 1
        key = next(iter(sv._background_tasks))
        assert key.startswith("turn-promotion:")
        assert "42" in key
        # Drain the task so the test doesn't leak a pending task.
        await sv.finalize_background_tasks(timeout_s=1.0)
    asyncio.run(_run())


def test_schedule_promotion_handles_missing_memory_manager():
    """Defensive: when supervisor has no memory_manager, the
    function returns without spawning a task or raising."""
    sv = GoatSupervisor(ServiceRegistry())
    sv.memory_manager = None
    schedule_promotion(sv, turn_count=1)
    assert len(sv._background_tasks) == 0


# ── Task exception visibility ─────────────────────────────────────────────


def test_task_exceptions_are_logged_at_warning():
    """If the promotion raises, the exception must be logged at
    WARNING (not DEBUG) so operators see recurring failures."""
    async def _run():
        sv = GoatSupervisor(ServiceRegistry())
        async def _boom():
            raise RuntimeError("promotion failed")
        sv.memory_manager = mock.MagicMock()
        sv.memory_manager.promote_turns = _boom

        with mock.patch.object(logging.getLogger(
                "goat2.supervisor.session.turn_persistence"
        ), "warning") as mock_warn:
            schedule_promotion(sv, turn_count=1)
            await sv.finalize_background_tasks(timeout_s=1.0)
            # The exception path must have logged at WARNING.
            assert any(
                "promotion failed" in str(c) or "promotion" in str(c).lower()
                for c in mock_warn.call_args_list
            ), f"expected warning log; got: {mock_warn.call_args_list}"
    asyncio.run(_run())


# ── Finalize: bounded drain ───────────────────────────────────────────────


def test_finalize_drains_pending_tasks_with_timeout():
    """Supervisor.finalize_session (or the new
    finalize_background_tasks) must await pending tasks so
    promotion work isn't lost. Bounded by a timeout so a stuck
    task can't block shutdown forever."""
    sv = GoatSupervisor(ServiceRegistry())
    completed: list[int] = []

    async def _work(n: int):
        await asyncio.sleep(0.01)
        completed.append(n)

    # Spawn three tasks directly via the helper.
    async def _spawn():
        for i in (1, 2, 3):
            t = asyncio.create_task(_work(i), name=f"turn-promotion:{i}")
            sv._background_tasks[f"turn-promotion:{i}"] = t
        # Drain with a small timeout — all three must complete.
        if hasattr(sv, "finalize_background_tasks"):
            await sv.finalize_background_tasks(timeout_s=2.0)
    asyncio.run(_spawn())
    assert sorted(completed) == [1, 2, 3]
    # Registry is now empty (drained).
    assert len(sv._background_tasks) == 0


# ── Regression: store_and_promote must not wrap schedule_promotion ────────


def test_store_and_promote_does_not_coroutine_wrap_schedule_promotion():
    """Regression: store_and_promote used to call

        asyncio.create_task(schedule_promotion(sv, n), name=...)

    but ``schedule_promotion`` is sync (def, not async def) and
    returns None. ``asyncio.create_task(None)`` raised
    "a coroutine was expected, got None" at runtime, surfacing
    as a 'store_and_promote failed' WARNING in the logs even
    though the actual work succeeded.

    The fix: ``store_and_promote`` now calls ``schedule_promotion``
    directly. ``schedule_promotion`` itself creates the
    background task and registers it in ``_background_tasks``,
    so the wrapping was redundant.

    We pin the contract here by inspecting the source — the
    test fails if anyone re-introduces the bad pattern.
    """
    import inspect
    from supervisor.session import turn_persistence as tp

    src = inspect.getsource(tp.store_and_promote)
    # The fix: store_and_promote must NOT call asyncio.create_task
    # with schedule_promotion as an argument.
    assert "asyncio.create_task(\n            schedule_promotion(" not in src, (
        "store_and_promote is wrapping schedule_promotion in "
        "asyncio.create_task — but schedule_promotion is sync and "
        "returns None. This is the 'a coroutine was expected, got None' "
        "regression."
    )
    # And it MUST call schedule_promotion directly (so the
    # background task actually gets registered).
    assert "schedule_promotion(supervisor, turn_count)" in src, (
        "store_and_promote must call schedule_promotion directly "
        "so the background task is registered for drain at "
        "session end (BUG-027)."
    )


def test_store_and_promote_registers_background_task():
    """Functional check: store_and_promote must register a
    background task via schedule_promotion. The task is
    observable in ``_background_tasks`` after the call."""
    from supervisor.session.turn_persistence import store_and_promote

    async def _run():
        sv = GoatSupervisor(ServiceRegistry())
        # Stub the persist + style phases so we only exercise
        # the schedule_promotion path.
        async def _noop_store(*args, **kwargs):
            return None
        async def _noop_learn(*args, **kwargs):
            return False
        sv.memory_manager = MagicMock()
        sv.memory_manager.store = AsyncMock(side_effect=_noop_store)
        sv.memory_manager.working = MagicMock()
        sv.memory_manager.working.list = AsyncMock(return_value=[])
        # Patch the inner functions so we don't hit real Letta/Chroma.
        with patch(
            "supervisor.session.turn_persistence._store_turn",
            new=_noop_store,
        ), patch(
            "supervisor.session.turn_persistence._learn_and_persist",
            new=AsyncMock(side_effect=_noop_learn),
        ):
            await store_and_promote(sv, turn_count=1, intent="x", summary="y")

        # The background task must be registered.
        assert any("turn-promotion" in k for k in sv._background_tasks), (
            f"schedule_promotion was not invoked from store_and_promote; "
            f"_background_tasks={list(sv._background_tasks)}"
        )
        # Drain so the test doesn't leak.
        await sv.finalize_background_tasks(timeout_s=1.0)
    asyncio.run(_run())