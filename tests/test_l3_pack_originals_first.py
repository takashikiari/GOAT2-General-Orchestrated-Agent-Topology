"""Wave 2 must never degrade the original retrieval (Laris, 2026-11-07): neighbours only fill the budget left."""
from __future__ import annotations

import logging

from memory import usage_probe as up
from memory.context_assembler import assemble_blocks, blended_gap_filter, fit_search_results
from memory.l3_attribution import InjectedBlocks
from memory.l3_pack import fit_originals_first, pack_lines


def _r(mid, score, words=20, **kw):
    return {"content": f"{mid} " + "cuvânt " * words, "blended_score": score,
            "metadata": {"message_id": mid, "timestamp": 1_700_000_000}, **kw}


def _scenario():
    """6 originals survive the gap filter; m0-m2 have 2 neighbours each (scored just below their seed)."""
    orig = [_r(f"m{i}", 0.9 - i * 0.01) for i in range(6)]
    nb = [_r(f"m{s}-n{k}", 0.9 - s * 0.01 - 1e-6 * (k + 1), mechanisms=["wave2"], wave2_of=f"m{s}")
          for s in range(3) for k in range(2)]
    return orig, blended_gap_filter(sorted(orig + nb, key=lambda r: r["blended_score"], reverse=True), 3.0)


def test_gap_filter_interleaves_neighbours_before_tail_originals():
    """The degradation path (found by laris/96ed8e): after the gap filter neighbours sit right after their seed."""
    _, relevant = _scenario()
    ids = [r["metadata"]["message_id"] for r in relevant]
    assert ids.index("m0-n0") < ids.index("m3")


def test_tight_budget_keeps_exactly_the_originals_that_fit_without_wave2():
    orig, relevant = _scenario()
    line_tok = pack_lines([orig[0]], 10_000)[2]
    for budget in range(line_tok, line_tok * 13):  # every pressure level, from one line to everything
        _, before, _ = pack_lines(orig, budget)
        _, _, packed, info = fit_originals_first(relevant, budget)
        after = [r for r in packed if "wave2" not in r.get("mechanisms", [])]
        assert [r["metadata"]["message_id"] for r in after] == [r["metadata"]["message_id"] for r in before]
        assert info["orig_replaced"] == []
        assert sum(pack_lines([r], 10_000)[2] for r in packed) <= max(budget, line_tok)


def test_neighbours_fill_leftover_and_only_for_seeds_that_made_it():
    orig, relevant = _scenario()
    line_tok = pack_lines([orig[0]], 10_000)[2]
    _, _, packed, _ = fit_originals_first(relevant, line_tok * 2)  # room: m0 + one more line
    ids = [r["metadata"]["message_id"] for r in packed]
    assert ids[0] == "m0" and "m1" in ids  # originals first: m1 beats m0's neighbours
    assert all(r.get("wave2_of") in ids for r in packed if "wave2" in r.get("mechanisms", []))
    _, _, packed, _ = fit_originals_first(relevant, line_tok * 12)
    ids = [r["metadata"]["message_id"] for r in packed]
    assert ids[:3] == ["m0", "m0-n0", "m0-n1"]  # with room, neighbours are shown next to their seed


def test_fit_search_results_unchanged_for_plain_lists():
    orig, _ = _scenario()
    block, n = fit_search_results(orig, 10_000)
    assert n == 6 and block.split("\n")[0].startswith("- [") and "m0 " in block.split("\n")[0]


def test_assemble_and_l3_usage_report_sets_and_budget_share(caplog):
    _, relevant = _scenario()
    prov: dict = {}
    blocks, _ = assemble_blocks(100_000, relevant, {}, "id", [], 3.0, None, prov)
    assert prov["orig_before"] == prov["orig_after"] and prov["orig_replaced"] == []
    assert prov["wave2_ranks"] and prov["l3_budget"] > 0
    caplog.set_level(logging.INFO, logger="memory.usage_probe")
    m = up.log_usage("c7", InjectedBlocks(blocks, prov), "răspuns")
    assert m["orig_replaced"] == [] and m["orig_before"] == m["orig_after"] == 6
    assert 0 < m["wave2_budget_pct"] < 100
    line = next(r.getMessage() for r in caplog.records if "chat=c7" in r.getMessage())
    assert "orig_before=6 orig_after=6 orig_replaced=[]" in line and "wave2_budget_pct=" in line
