"""tests.test_conversation_runner — full-cycle warm vs cold benchmark turns (spec §4.4)."""
from __future__ import annotations

import asyncio

from benchmark.conversation_runner import run_conversation
from memory.analytics import MemoryAnalytics
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeLayers, _FakeRegistry, _LLMClient


def _make_orchestrator(layers, reply="ok"):
    # A real MemoryAnalytics is required here (not _orch_fakes' _FakeAnalytics):
    # snapshot_analytics reads counters (cache_hits, warm_served_turns, ...)
    # that _FakeAnalytics doesn't define — same reasoning as
    # tests/test_benchmark_context_capture.py.
    registry = _FakeRegistry(layers, _LLMClient(_Completions(reply)), MemoryAnalytics())
    orch = Orchestrator(
        layers=registry.memory_layers, llm_client=registry.llm_client,
        plugin_manager=registry.plugin_manager, analytics=registry.memory_analytics, tools=[],
    )
    return orch, registry


def test_run_conversation_uses_distinct_chat_ids_for_warm_and_cold():
    layers = _FakeLayers()
    orch, registry = _make_orchestrator(layers)
    case = {"id": "c1", "query": "what time is the meeting", "expected_fact": "9am", "lead_in_turns": ["The meeting is at 9am."]}
    result = asyncio.run(run_conversation(orch, registry, case))
    assert result["warm"]["chat_id"] != result["cold"]["chat_id"]


def test_run_conversation_preloads_lead_in_via_store_episodic():
    layers = _FakeLayers()
    orch, registry = _make_orchestrator(layers)
    case = {"id": "c1", "query": "what time is the meeting", "expected_fact": "9am", "lead_in_turns": ["The meeting is at 9am."]}
    asyncio.run(run_conversation(orch, registry, case))
    assert layers.archive_calls >= 1  # store_episodic was called for the lead-in


def test_run_conversation_returns_response_context_blocks_and_groundedness_for_both_paths():
    layers = _FakeLayers(blocks=["[Identity]\nYou are GOAT.", "[Recall]\nThe meeting is at 9am."])
    orch, registry = _make_orchestrator(layers, reply="It's at 9am.")
    case = {"id": "c1", "query": "what time is the meeting", "expected_fact": "9am", "lead_in_turns": ["The meeting is at 9am."]}
    result = asyncio.run(run_conversation(orch, registry, case))
    for path in ("warm", "cold"):
        turn = result[path]
        assert turn["response"] == "It's at 9am."
        assert isinstance(turn["context_blocks"], list) and turn["context_blocks"]
        assert isinstance(turn["warm_served"], bool)
        assert "grounded" in turn["groundedness"]


def test_run_conversation_defaults_lead_in_to_expected_fact_when_absent():
    layers = _FakeLayers()
    orch, registry = _make_orchestrator(layers)
    case = {"id": "c2", "query": "q", "expected_fact": "the fact"}  # no lead_in_turns key
    result = asyncio.run(run_conversation(orch, registry, case))
    assert result["warm"]["response"] == "ok"
