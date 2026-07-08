"""tests.test_orchestrator_context_capture — opt-in raw context_blocks capture.

Spec §10.3: the groundedness judge (benchmark §4.6) needs the actual raw L3
text fed to the LLM this turn, not just MemoryAnalytics counters. Orchestrator.run
gains an optional on_context_assembled callback, invoked once per turn with the
exact context_blocks list assembled — a side channel, separate from the always-
logged MemoryObservation (which stays privacy-truncated and unchanged).
"""
from __future__ import annotations

import asyncio

from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient


def _make_orch(layers):
    llm = _LLMClient(_Completions("ok"))
    return Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())


def test_on_context_assembled_receives_exact_context_blocks():
    blocks = ["[Identity]\nYou are GOAT.", "[Recall]\nsome L3 fact"]
    layers = _FakeLayers(blocks=blocks)
    orch = _make_orch(layers)
    captured: list[list[str]] = []
    asyncio.run(orch.run("hello", "chat1", on_context_assembled=captured.append))
    assert len(captured) == 1
    assert captured[0] == blocks


def test_on_context_assembled_is_optional_and_defaults_to_no_op():
    """Existing callers (no callback passed) must be unaffected."""
    layers = _FakeLayers(results=[])
    orch = _make_orch(layers)
    reply = asyncio.run(orch.run("hello", "chat1"))
    assert reply == "ok"
