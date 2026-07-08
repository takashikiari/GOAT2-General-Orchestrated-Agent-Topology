"""tests.test_benchmark_context_capture — BenchmarkRunner surfaces raw context_blocks.

Spec §10.3: the groundedness judge (benchmark §4.6, not yet built) needs the
actual L3 text fed to the LLM, not just MemoryAnalytics counters. run_single
now captures it via Orchestrator.run's on_context_assembled callback and
threads it through _diff/_score into the final result dict, alongside
warm_served (a cumulative counter that was trivially diffable but previously
dropped by _snapshot/_diff).
"""
from __future__ import annotations

import asyncio

from benchmark.runner import BenchmarkRunner
from memory.analytics import MemoryAnalytics
from tests._orch_fakes import _Completions, _FakeLayers, _FakeRegistry, _LLMClient


def test_run_single_surfaces_context_blocks_and_warm_served():
    blocks = ["[Identity]\nYou are GOAT.", "[Recall]\nsome L3 fact"]
    layers = _FakeLayers(blocks=blocks)
    reg = _FakeRegistry(layers, _LLMClient(_Completions("reply")), MemoryAnalytics())
    runner = BenchmarkRunner(registry=reg)
    result = asyncio.run(runner.run_single({"id": "t1", "name": "t", "query": "hello world"}))
    assert result["context_blocks"] == blocks
    assert "warm_served" in result
