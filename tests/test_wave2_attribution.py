"""Wave-2 attribution in l3_usage: which injected L3 entries were wave-2 neighbours, and were they used."""
from __future__ import annotations

import asyncio
import logging

from memory import usage_probe as up
from memory.context_assembler import assemble_blocks, fit_search_results
from memory.l3_attribution import InjectedBlocks, mechanism_ranks, pack_order


def _r(content, score, ts, mech=None, mid=None, wave2_of=None):
    r = {"content": content, "blended_score": score, "metadata": {"timestamp": ts, "message_id": mid}}
    if mech:
        r["mechanisms"] = [mech]
    if wave2_of:
        r["wave2_of"] = wave2_of
    return r


def _results():
    seed = _r("Laris a configurat serverul Letta pe portul 8283", 0.9, 1_700_000_000, mid="m1")
    other = _r("rețetă de clătite cu banane", 0.88, 1_700_100_000, mid="m2")
    neigh = _r("după aceea a repornit containerul Letta și a verificat portul", 0.9 - 1e-6, 1_700_000_060,
               mech="wave2", wave2_of="m1", mid="m3")
    return [seed, neigh, other]


def test_pack_order_is_the_block_line_order():
    res = _results() + [_r("eveniment din fereastra cerută", 0.1, 1_699_000_000, mech="temporal", mid="t")]
    block, n = fit_search_results(res, 10_000)
    lines = block.split("\n")
    assert n == len(lines) == 4
    assert [ln.split("] ", 1)[1] for ln in lines] == [r["content"] for r in pack_order(res)]


def test_assemble_reports_wave2_rank_and_keeps_model_text_identical():
    prov: dict = {}
    with_prov, used_a = assemble_blocks(4000, _results(), {}, "id", [], 3.0, None, prov)
    without, used_b = assemble_blocks(4000, _results(), {}, "id", [], 3.0, None)
    assert with_prov == without and used_a == used_b == 3
    l3 = with_prov[-1].split("\n")[1:]
    assert prov["wave2_ranks"] == [2] and "repornit containerul" in l3[1]
    assert prov["temporal_ranks"] == []


def test_neighbour_cut_by_budget_has_no_rank():
    for budget in range(10, 400):  # smallest budget that packs exactly the seed line
        prov: dict = {}
        blocks, used = assemble_blocks(budget, _results(), {}, "id", [], 3.0, None, prov)
        if used == 1:
            break
    assert used == 1 and "portul 8283" in blocks[-1]
    assert prov["wave2_ranks"] == []  # the neighbour (would be rank 2) did not fit


def test_attribute_counts_used_neighbours():
    blocks = InjectedBlocks(["id", up.L3_HEADER + "\n- [a] Letta pe portul 8283 serverul configurat\n"
                                              "- [b] containerul Letta repornit verificat portul\n- [c] clătite banane"],
                            {"wave2_ranks": [2], "temporal_ranks": []})
    m = up.measure(blocks, "Am repornit containerul Letta și am verificat portul 8283 pe server.")
    a = up.attribute(m, blocks.provenance)
    assert a["wave2_ranks"] == [2] and a["wave2_used"] == 1 and a["wave2_tokens"] == m["entries"][1]["tokens"]
    assert up.attribute(m, None) == {"wave2_ranks": None, "wave2_used": None, "wave2_tokens": None}
    assert up.attribute(m, {"wave2_ranks": [9]})["wave2_ranks"] == []  # out-of-range rank ignored


def test_log_line_carries_wave2_fields(caplog):
    caplog.set_level(logging.INFO, logger="memory.usage_probe")
    blocks = InjectedBlocks(["id", up.L3_HEADER + "\n- [a] unu doi trei\n- [b] patru cinci"], {"wave2_ranks": []})
    up.log_usage("c9", blocks, "nimic")
    line = next(r.getMessage() for r in caplog.records if "chat=c9" in r.getMessage())
    assert line.endswith("wave2_ranks=[] wave2_used=0 wave2_tokens=0")
    up.log_usage("c8", ["id", up.L3_HEADER + "\n- [a] x y z"], "nimic")  # plain list: unknown, not zero
    assert next(r.getMessage() for r in caplog.records if "chat=c8" in r.getMessage()).endswith(
        "wave2_ranks=None wave2_used=None wave2_tokens=None")


def test_orchestrator_hands_provenance_to_the_callback():
    from orchestrator.orchestrator import Orchestrator
    from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient, _Completions

    class _Layers(_FakeLayers):
        async def assemble_context(self, chat_id, provenance=None, **kw):
            if provenance is not None:
                provenance["wave2_ranks"] = [2]
            return ["id", up.L3_HEADER + "\n- [a] x\n- [b] y"], 2

    got = []
    orch = Orchestrator(layers=_Layers(results=[]), llm_client=_LLMClient(_Completions("ok")),
                        plugin_manager=_FakePluginManager(), analytics=_FakeAnalytics(), tools=[])
    asyncio.run(orch.run("q", "chat", on_context_assembled=got.append))
    assert isinstance(got[0], InjectedBlocks) and got[0].provenance["wave2_ranks"] == [2]
    assert list(got[0])[0] == "id"


def test_mechanism_ranks():
    assert mechanism_ranks([{"mechanisms": ["wave2"]}, {}, {"mechanisms": ["wave2", "x"]}], "wave2") == [1, 3]
