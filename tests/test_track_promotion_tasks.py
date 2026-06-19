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