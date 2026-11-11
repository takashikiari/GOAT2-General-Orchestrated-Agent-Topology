"""tests.test_tool_gate — the prefetch quality line every turn, and the gate that hides search_memory when the prefetch is
already good (memory tools = fallback; Laris via GOAT, 2026-11-11)."""
from __future__ import annotations

import asyncio
import logging

from orchestrator import tool_gate
from orchestrator.orchestrator import Orchestrator
from orchestrator.tool_gate import GateConfig, decide, measure
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakeRegistry, _LLMClient
from orchestrator.tools import ToolDefinition


def _r(score, wave2=False):
    return {"content": "x", "metadata": {"timestamp": 0.0}, "blended_score": score,
            **({"mechanisms": ["wave2"]} if wave2 else {})}


_REAL_MEASURE = tool_gate.measure
ON = GateConfig(enabled=True, min_top_score=0.5)
OFF = GateConfig(enabled=False, min_top_score=0.5)


def test_quality_is_the_top_rerank_score_the_top_three_and_the_counts_wave2_neighbours_aside():
    q = measure([_r(0.3), _r(0.9), _r(0.95, wave2=True), _r(0.6)], used=3, tokens=420, reranked=True)
    assert (q.top, round(q.top3, 2), q.found, q.used, q.tokens, q.reranked) == (0.9, 1.8, 4, 3, 420, True)
    empty = measure([], used=0, tokens=0, reranked=True)
    assert (empty.top, empty.found) == (None, 0)


def test_a_good_prefetch_would_hide_the_tool_and_only_hides_it_when_the_gate_is_on():
    q = measure([_r(0.98)], used=1, tokens=100, reranked=True)
    assert decide(q, OFF) == {"would_hide": True, "hidden": False, "why": "top>=0.5"}
    assert decide(q, ON) == {"would_hide": True, "hidden": True, "why": "top>=0.5"}


def test_the_tool_stays_whenever_the_prefetch_is_weak_empty_unused_or_not_reranked():
    weak = measure([_r(0.2)], used=1, tokens=50, reranked=True)
    unused = measure([_r(0.9)], used=0, tokens=0, reranked=True)
    nothing = measure([], used=0, tokens=0, reranked=True)
    unranked = measure([_r(0.9)], used=1, tokens=50, reranked=False)
    assert decide(weak, ON) == {"would_hide": False, "hidden": False, "why": "top<0.5"}
    assert decide(unused, ON) == {"would_hide": False, "hidden": False, "why": "l3_used=0"}
    assert decide(nothing, ON) == {"would_hide": False, "hidden": False, "why": "no_results"}
    # no cross-encoder score to judge by: the fallback stays exactly when the prefetch may be degraded
    assert decide(unranked, ON) == {"would_hide": False, "hidden": False, "why": "no_rerank"}


def test_the_settings_come_from_memory_toml_with_a_conservative_default():
    assert tool_gate.load({}) == GateConfig(enabled=False, min_top_score=0.5)
    assert tool_gate.load({"tool_gate": {"enabled": True, "search_memory_min_top_score": 0.7}}) == GateConfig(True, 0.7)
    assert tool_gate.CONFIG == tool_gate.load(__import__("memory.config", fromlist=["_cfg"])._cfg)


def test_one_log_line_per_turn_with_the_numbers_and_the_decision(caplog):
    q = measure([_r(0.981), _r(0.95), _r(0.948)], used=3, tokens=900, reranked=True)
    with caplog.at_level(logging.INFO, logger="orchestrator.tool_gate"):
        tool_gate.log_line("42", q, decide(q, OFF), OFF)
    [line] = [r.getMessage() for r in caplog.records if r.name == "orchestrator.tool_gate"]
    assert line == ("prefetch_quality chat=42 top=0.981 top3=2.879 found=3 used=3 l3_tokens=900 reranked=1 "
                    "gate=off min_top=0.5 would_hide=1 hidden=0 why=top>=0.5")


class _Recorder(_Completions):
    def __init__(self):
        super().__init__("ok")
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        return await super().create(**kw)


class _RankedLayers(_FakeLayers):
    reranks = True


def _tool(name):
    async def handler(**_kw):
        return "r"
    return ToolDefinition(name=name, description=name, parameters={"type": "object", "properties": {}}, handler=handler)


def _run(monkeypatch, cfg, score):
    import memory.config as mcfg
    monkeypatch.setattr(mcfg, "L3_MIN_ALNUM", 0)
    monkeypatch.setattr(tool_gate, "CONFIG", cfg)
    # the fake layers have no cross-encoder, so their scores are not the reranker's: this test checks the wiring
    # (decision -> tools and guidance of the turn); measure() itself is tested above
    monkeypatch.setattr(tool_gate, "measure", lambda results, used, tokens, reranked: _REAL_MEASURE(
        [dict(r, blended_score=score) for r in results], used, tokens, reranked))
    llm = _Recorder()
    layers = _RankedLayers(results=[{"content": "m", "metadata": {"timestamp": 0.0}, "score": 0.5, "blended_score": score}], l3_used=1)
    reg = _FakeRegistry(layers, _LLMClient(llm), _FakeAnalytics())
    orch = Orchestrator(layers=reg.memory_layers, llm_client=reg.llm_client, plugin_manager=reg.plugin_manager,
                        analytics=reg.memory_analytics, tools=[_tool("search_memory"), _tool("store_memory")])
    asyncio.run(orch.run("ce am decis ieri despre voce", "c"))
    first = llm.calls[0]
    names = [t["function"]["name"] for t in first.get("tools", [])]
    system = first["messages"][0]["content"]
    return names, system


def test_with_the_gate_on_a_good_prefetch_takes_search_memory_and_its_guidance_out_of_the_turn(monkeypatch):
    names, system = _run(monkeypatch, ON, 0.98)
    assert names == ["store_memory"] and "search_memory is a last resort" not in system


def test_with_the_gate_off_or_a_weak_prefetch_the_turn_is_unchanged(monkeypatch):
    for cfg, score in ((OFF, 0.98), (ON, 0.1)):
        names, system = _run(monkeypatch, cfg, score)
        assert names == ["search_memory", "store_memory"] and "search_memory is a last resort" in system
