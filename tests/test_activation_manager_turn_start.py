"""tests.test_activation_manager_turn_start — update_activation's turn_start wiring.

Bug 2 (2026-07-12, write race): ActivationStore.set's compare-and-set guard
(tests/test_activation_store_cas.py) only works if the ``Activation.ts`` it
compares is the writing turn's ORIGIN timestamp (orchestrator.run()'s
``start``, captured before any I/O) rather than this background prefetch
task's own completion time — otherwise a slow turn N's write would carry a
LARGER (later) ts than a fast turn N+1's write purely because it finished
computing later, defeating the guard entirely (the exact inversion the CAS
guard exists to prevent). These tests pin that ``update_activation`` writes
whatever ``turn_start`` it is given into ``Activation.ts``, for both the warm
in-place-update path and the cold/drift new-Activation path.
"""
from __future__ import annotations

import asyncio

from memory.activation import Activation
from orchestrator.activation_manager import update_activation


class _FakeLayers:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, object]] = []

    async def set_activation(self, chat_id, activation) -> None:
        self.set_calls.append((chat_id, activation))


def test_turn_start_becomes_ts_on_cold_write():
    layers = _FakeLayers()
    result = asyncio.run(update_activation(
        layers, "chat1", "hello", [0.1, 0.2], "cold", None, [{"content": "x"}],
        turn_start=12345.0,
    ))
    assert result.ts == 12345.0


def test_turn_start_becomes_ts_on_drift_write():
    layers = _FakeLayers()
    activation = Activation(centroid=[0.1, 0.2], topic_id="t1", turn_count=2)
    result = asyncio.run(update_activation(
        layers, "chat1", "kinda related", [0.15, 0.25], "drift", activation, [{"content": "y"}],
        turn_start=999.5,
    ))
    assert result.ts == 999.5


def test_turn_start_becomes_ts_on_warm_write():
    layers = _FakeLayers()
    activation = Activation(centroid=[0.1, 0.2], topic_id="t1", turn_count=2, ts=1.0)
    result = asyncio.run(update_activation(
        layers, "chat1", "hello again", [0.1, 0.2], "warm", activation, [],
        turn_start=555.0,
    ))
    assert result.ts == 555.0


def test_omitted_turn_start_falls_back_to_wall_clock():
    """Backward-compat: callers/tests that don't pass turn_start (e.g. the
    pre-existing test_activation_manager_logging.py suite) keep the old
    completion-time behaviour."""
    import time

    layers = _FakeLayers()
    before = time.time()
    result = asyncio.run(update_activation(
        layers, "chat1", "hello", [0.1, 0.2], "cold", None, [{"content": "x"}],
    ))
    after = time.time()
    assert before <= result.ts <= after
