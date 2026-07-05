"""tests.test_orchestrator_memory_flow — unconditional search + cache_key reporting."""
from __future__ import annotations

import asyncio

from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import (
    _Completions, _FakeAnalytics, _FakeLayers, _FakeRegistry, _LLMClient,
)


def test_search_runs_unconditionally_and_reports_cache_key():
    """Intent containing 'la' used to drop confidence to 0.2 and skip search."""
    intent = "Pai și după atâtea tokens prefetchul a folosit 0 blocks la fiecare qwery"
    layers = _FakeLayers(results=[{"content": "m", "metadata": {"timestamp": 0.0}, "score": 0.5}])
    reg = _FakeRegistry(layers, _LLMClient(_Completions("reply")), _FakeAnalytics())
    reply = asyncio.run(Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[]).run(intent, "chat"))
    assert layers.search_calls == 1                # search ran despite low confidence
    assert reply == "reply"
    obs = reg.memory_analytics.records[-1]
    assert obs.cache_key == "search:deadbeef"       # cache key now reported (was null)
    assert obs.prefetch_attempted is True
    assert obs.prefetch_succeeded is True


def test_prefetch_blocks_used_reflects_real_l3_used():
    """The hardcoded 0 is replaced by the actual count assembled into context."""
    layers = _FakeLayers(
        results=[{"content": "m", "metadata": {"timestamp": 0.0}, "score": 0.5}],
        l3_used=3,
    )
    reg = _FakeRegistry(layers, _LLMClient(_Completions("r")), _FakeAnalytics())
    asyncio.run(Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[]).run("what is X", "c"))
    obs = reg.memory_analytics.records[-1]
    assert obs.prefetch_blocks_used == 3            # no longer 0
    assert obs.results_used == 3


def test_archive_turn_calls_store_episodic():
    """_archive_turn must call store_episodic on every turn.

    _FakeLayers lacked store_episodic when _archive_turn was introduced,
    causing a WARNING log on every test run (the missing method raised
    AttributeError inside _archive_turn's except block, which silently
    emitted to the shared log file). This test catches that class of gap:
    if _FakeLayers ever drops store_episodic again, it fails immediately
    instead of polluting logs.
    """
    layers = _FakeLayers(results=[])
    reg = _FakeRegistry(layers, _LLMClient(_Completions("reply")), _FakeAnalytics())
    asyncio.run(Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[]).run("hello", "c"))
    assert layers.archive_calls >= 1


def test_latency_split_llm_vs_inject():
    """The LLM call is isolated in latency_llm; inject is just prompt build."""
    layers = _FakeLayers(results=[])
    # 20ms LLM call so latency_llm is measurably the dominant stage.
    reg = _FakeRegistry(layers, _LLMClient(_Completions("r", delay=0.02)), _FakeAnalytics())
    asyncio.run(Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager, analytics=reg.memory_analytics, tools=[]).run("hello", "c"))
    obs = reg.memory_analytics.records[-1]
    assert obs.latency_llm >= 0.015                 # the LLM call, isolated
    assert obs.latency_inject < 0.01                # prompt build only — no longer the 30s
    assert obs.latency_save >= 0.0
    assert obs.latency_llm + obs.latency_inject + obs.latency_save <= obs.latency_total + 0.05