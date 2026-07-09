"""tests.test_activation_manager_logging — update_activation logs its own
cold/warm/drift decision at INFO level.

Root cause (2026-07-09 investigation): the activation transition (state,
topic_id, turn_count) was only visible buried inside the per-turn
observability JSON blob -- update_activation itself had no log line of its
own (only a conditional "topic return" message), unlike run_prefetch_and_save's
"prefetch ok" line. This made manual log verification (grep for what happened)
much harder than it needed to be.
"""
from __future__ import annotations

import asyncio
import logging

from orchestrator.activation_manager import update_activation


class _FakeLayers:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, object]] = []

    async def set_activation(self, chat_id, activation) -> None:
        self.set_calls.append((chat_id, activation))


def test_logs_cold_turn_with_new_topic_id(caplog) -> None:
    layers = _FakeLayers()
    with caplog.at_level(logging.INFO, logger="orchestrator.activation_manager"):
        result = asyncio.run(update_activation(
            layers, "chat1", "hello", [0.1, 0.2], "cold", None, [{"content": "x"}],
        ))

    assert result is not None
    assert any("activation" in r.message and "state=cold" in r.message for r in caplog.records)
    assert any(f"topic={result.topic_id}" in r.message for r in caplog.records)


def test_logs_warm_turn(caplog) -> None:
    from memory.activation import Activation

    layers = _FakeLayers()
    activation = Activation(centroid=[0.1, 0.2], topic_id="t1", turn_count=2)
    with caplog.at_level(logging.INFO, logger="orchestrator.activation_manager"):
        asyncio.run(update_activation(
            layers, "chat1", "hello again", [0.1, 0.2], "warm", activation, [],
        ))

    assert any("state=warm" in r.message and "topic=t1" in r.message for r in caplog.records)


def test_logs_drift_turn(caplog) -> None:
    from memory.activation import Activation

    layers = _FakeLayers()
    activation = Activation(centroid=[0.1, 0.2], topic_id="t1", turn_count=2)
    with caplog.at_level(logging.INFO, logger="orchestrator.activation_manager"):
        asyncio.run(update_activation(
            layers, "chat1", "kinda related", [0.15, 0.25], "drift", activation, [{"content": "y"}],
        ))

    assert any("state=drift" in r.message and "topic=t1" in r.message for r in caplog.records)
