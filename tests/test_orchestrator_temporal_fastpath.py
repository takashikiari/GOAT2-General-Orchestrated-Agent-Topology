"""tests.test_orchestrator_temporal_fastpath — synchronous temporal search fires
on the CURRENT turn's query, not just via the next turn's background prefetch.

Regression test for the core bug found in the 2026-07-12 pipeline audit:
orchestrator.run()'s search stage only ever read activation.merged (populated
by the PREVIOUS turn's post-turn prefetch daemon, searching the PREVIOUS
turn's query). A query naming an explicit date for the FIRST time in a
conversation therefore got zero temporal context in its own reply — the
correct search only ran afterward, preparing context for the turn AFTER
that. These tests prove the fix: parse_interval(intent) gates a synchronous,
targeted temporal_candidates() search that merges into l3_results for THIS
turn, before assemble_context runs.
"""
from __future__ import annotations

import asyncio
import time

from memory.activation import Activation
from memory.temporal_route import parse_interval
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import (
    _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient,
)

_INTENT = "Ce am discutat pe 9 iulie?"


class _TemporalLayers(_FakeLayers):
    """search_episodic only returns the target when called with the window
    matching THIS turn's query — proving the orchestrator actually ran a
    fresh, targeted search rather than replaying stale activation state.
    """

    def __init__(self, activation, after, before, target):
        super().__init__()
        self._activation = activation
        self._after = after
        self._before = before
        self._target = target
        self.captured_l3_results: list[dict] = []
        self.temporal_search_calls = 0

    async def get_activation(self, chat_id):
        return self._activation

    async def search_episodic(self, query, limit=5, after=None, before=None,
                               topic_id=None, chat_id_filter=None):
        self.search_calls += 1
        if after is not None and before is not None:
            self.temporal_search_calls += 1
            if abs(after - self._after) < 2 and abs(before - self._before) < 2:
                return [self._target]
            return []
        # Non-temporal search calls (e.g. from the background prefetch daemon,
        # which may or may not get a chance to run before the test's event
        # loop closes) — irrelevant to this test, return nothing.
        return []

    async def assemble_context(self, chat_id, budget=None, l3_results=None,
                                facts=None, messages=None, identity_prompt=None):
        self.captured_l3_results = list(l3_results or [])
        ids = ",".join(
            str(r.get("metadata", {}).get("message_id")) for r in (l3_results or [])
        )
        block = f"[Context recuperat din istoric]\n{ids}"
        return (["[Identity]\nYou are GOAT.", block], len(l3_results or []))


def _make_activation_with_unrelated_prefetch() -> Activation:
    """Simulates the state the bug produces: the PREVIOUS turn's post-turn
    prefetch already ran (for a DIFFERENT, unrelated query) and left its
    results in activation.merged. Nothing about "9 iulie" is in here.
    """
    return Activation(
        merged=[{
            "content": "unrelated previous-turn topic",
            "metadata": {"message_id": "prev-1", "timestamp": time.time()},
            "blended_score": 0.9,
            "mechanisms": ["semantic_global"],
        }],
        topic_id="topic-prev",
        last_query="ce facem azi",
    )


def test_first_time_date_query_gets_synchronous_temporal_context():
    """The FIRST time a date is named, its temporal search must still run
    synchronously and land in THIS turn's assembled context — not just
    inform a future turn's background prefetch.
    """
    interval = parse_interval(_INTENT)
    assert interval is not None, "test fixture requires parse_interval to find a date in this query"
    after, before = interval
    target = {
        "content": "TARGET: discutie despre planuri pe 9 iulie",
        "metadata": {"message_id": "target-1", "timestamp": (after + before) / 2},
        "score": 0.1,
    }
    activation = _make_activation_with_unrelated_prefetch()
    layers = _TemporalLayers(activation, after, before, target)
    llm = _LLMClient(_Completions("reply"))
    orch = Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())

    reply = asyncio.run(orch.run(_INTENT, "chat1"))

    assert reply == "reply"
    assert layers.temporal_search_calls >= 1, "synchronous temporal search never ran this turn"
    result_ids = {r.get("metadata", {}).get("message_id") for r in layers.captured_l3_results}
    assert "target-1" in result_ids, (
        "the CURRENT turn's temporal search result must be merged into "
        f"l3_results before assemble_context runs; got {result_ids}"
    )
    # Additive, not a replacement: the existing warm-served result must survive.
    assert "prev-1" in result_ids


def test_temporal_fresh_result_carries_temporal_mechanism_tag():
    """The freshly-found temporal candidate must be tagged 'temporal' in
    mechanisms so blended_gap_filter's temporal-rescue protection (already
    fixed separately) applies to it too, not just to background-prefetch
    temporal hits.
    """
    interval = parse_interval(_INTENT)
    after, before = interval
    target = {
        "content": "TARGET: discutie despre planuri pe 9 iulie",
        "metadata": {"message_id": "target-1", "timestamp": (after + before) / 2},
        "score": 0.1,
    }
    activation = _make_activation_with_unrelated_prefetch()
    layers = _TemporalLayers(activation, after, before, target)
    llm = _LLMClient(_Completions("reply"))
    orch = Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())

    asyncio.run(orch.run(_INTENT, "chat1"))

    by_id = {r["metadata"]["message_id"]: r for r in layers.captured_l3_results}
    assert "temporal" in by_id["target-1"].get("mechanisms", [])


def test_no_temporal_expression_leaves_existing_behaviour_unchanged():
    """A query with no date/time expression must not trigger any extra search
    — the existing warm-serving/background-prefetch behaviour is untouched.
    """
    intent = "Care a fost cauza confuziei cu logurile?"
    assert parse_interval(intent) is None
    activation = _make_activation_with_unrelated_prefetch()
    layers = _TemporalLayers(activation, after=0.0, before=0.0, target={
        "content": "should never be returned",
        "metadata": {"message_id": "should-not-appear"},
    })
    llm = _LLMClient(_Completions("reply"))
    orch = Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())

    asyncio.run(orch.run(intent, "chat1"))

    assert layers.temporal_search_calls == 0
    result_ids = {r.get("metadata", {}).get("message_id") for r in layers.captured_l3_results}
    assert result_ids == {"prev-1"}
