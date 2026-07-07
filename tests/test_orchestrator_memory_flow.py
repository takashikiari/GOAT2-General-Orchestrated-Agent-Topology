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
    assert layers.search_calls == 2                # thematic + thematic_scoped both ran
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


# --- topic_id flows through archive -----------------------------------------

from memory.activation import Activation
from tests._orch_fakes import _FakePluginManager


class _TopicCaptureLayers(_FakeLayers):
    """Extends _FakeLayers to capture topic_id passed to store_episodic."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stored_topic_ids: list[str] = []
        self._activation_store: Activation | None = None

    async def store_episodic(self, chat_id: str, content: str, tags=None, topic_id: str = "", doc_id: str | None = None) -> str:
        self.stored_topic_ids.append(topic_id)
        self.archive_calls += 1
        return doc_id or str(__import__("uuid").uuid4())

    async def set_activation(self, chat_id, activation):
        self._activation_store = activation
        self.set_activation_calls = getattr(self, "set_activation_calls", 0) + 1

    async def embed_query(self, query):
        # Return a non-None embedding so turn_state can be computed
        return [1.0, 0.0]


def _make_orch(layers):
    llm = _LLMClient(_Completions("ok"))
    return Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())


def test_archive_turn_receives_topic_id_after_cold_turn():
    """On a cold turn a fresh topic_id must be generated and passed to store_episodic."""
    layers = _TopicCaptureLayers()
    orch = _make_orch(layers)
    asyncio.run(orch.run("hello world", "chat1"))
    # At least one store_episodic call should have a non-empty topic_id
    assert any(tid for tid in layers.stored_topic_ids), \
        "expected a non-empty topic_id in at least one store_episodic call"


def test_topic_id_is_uuid_format():
    """Generated topic_id must be a valid UUID string (8-4-4-4-12 hex)."""
    import re
    UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    layers = _TopicCaptureLayers()
    orch = _make_orch(layers)
    asyncio.run(orch.run("hello world", "chat1"))
    non_empty = [tid for tid in layers.stored_topic_ids if tid]
    assert non_empty, "no non-empty topic_ids stored"
    assert UUID_RE.match(non_empty[0]), f"topic_id not UUID format: {non_empty[0]!r}"


# --- identity_prompt flows through assemble_context --------------------------

def test_identity_prompt_is_fetched_and_used():
    """Orchestrator must call get_identity_prompt and pass it to assemble_context."""
    identity_used = []

    class _IdentityCaptureLayers(_FakeLayers):
        async def get_identity_prompt(self):
            return "Custom identity for this test."

        async def assemble_context(self, chat_id, budget=None, l3_results=None,
                                   facts=None, messages=None, identity_prompt=None):
            identity_used.append(identity_prompt)
            return list(self._blocks), self._l3_used

    layers = _IdentityCaptureLayers()
    llm = _LLMClient(_Completions("ok"))
    orch = Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())
    asyncio.run(orch.run("hello", "chat1"))
    assert identity_used, "assemble_context was never called"
    assert identity_used[0] == "Custom identity for this test.", (
        f"expected custom identity, got {identity_used[0]!r}"
    )
