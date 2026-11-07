"""Phase 2 (memory spec v4): wave 2 — same-topic neighbours of the top seeds, never changing seed selection."""
import asyncio

from memory.context_assembler import blended_gap_filter
from memory.wave2 import add_neighbors


def _r(mid, score, topic="t", seq=10, **kw):
    return {"content": f"text {mid}", "metadata": {"message_id": mid, "topic_id": topic, "sequence_number": seq},
            "blended_score": score, **kw}


class _Ep:
    def __init__(self, pool):
        self.pool, self.calls = pool, []

    async def neighbors(self, topic, seq, window):
        self.calls.append((topic, seq, window))
        return [n for n in self.pool if n["metadata"]["topic_id"] == topic
                and abs(n["metadata"]["sequence_number"] - seq) <= window]


def test_neighbours_closest_first_capped_deduped_and_tagged():
    ranked = [_r("s1", 0.9, seq=10), _r("s2", 0.8, seq=50), _r("s3", 0.7, seq=90), _r("s4", 0.6, seq=120)]
    pool = [_r("n12", 0, seq=12), _r("n9", 0, seq=9), _r("n11", 0, seq=11), _r("s2", 0, seq=50)]
    ep = _Ep(pool)
    out = asyncio.run(add_neighbors(ep, ranked, seeds=3, per_seed=2, window=2))
    extra = out[len(ranked):]
    assert [e["metadata"]["message_id"] for e in extra] == ["n9", "n11"]  # |Δseq|=1 first, capped at 2
    assert all(e["mechanisms"] == ["wave2"] and e["wave2_of"] == "s1" for e in extra)
    assert all(e["blended_score"] < 0.9 for e in extra) and len(ep.calls) == 3  # only the top 3 seeds


def test_no_topic_or_sequence_means_no_neighbours():
    ranked = [{"content": "x", "metadata": {"message_id": "a"}, "blended_score": 0.5}]
    assert asyncio.run(add_neighbors(_Ep([]), ranked, 3, 2, 2)) == ranked


def test_lookup_error_never_costs_the_retrieval():
    class _Boom:
        async def neighbors(self, *a):
            raise RuntimeError("down")
    ranked = [_r("s1", 0.9)]
    assert asyncio.run(add_neighbors(_Boom(), ranked, 3, 2, 2)) == ranked


def test_gap_filter_keeps_neighbours_only_of_kept_seeds_and_same_seeds():
    # gaps .01 .01 .63 .01 .01 → mean .134, max .63 ≥ 3×mean → cut after the first three
    base = [_r("a", 0.95), _r("b", 0.94), _r("e", 0.93), _r("c", 0.30), _r("d", 0.29), _r("f", 0.28)]
    plain = [r["metadata"]["message_id"] for r in blended_gap_filter([dict(r) for r in base])]
    nb_a = dict(_r("na", 0.95 - 1e-6), mechanisms=["wave2"], wave2_of="a")
    nb_c = dict(_r("nc", 0.30 - 1e-6), mechanisms=["wave2"], wave2_of="c")
    out = [r["metadata"]["message_id"] for r in blended_gap_filter([dict(r) for r in base] + [nb_a, nb_c])]
    assert plain == ["a", "b", "e"]
    assert [m for m in out if m in ("a", "b", "c", "d", "e", "f")] == plain  # seeds unchanged by the neighbours
    assert "na" in out and "nc" not in out and out.index("na") == out.index("a") + 1


def test_episodic_neighbors_query(tmp_path):
    import chromadb
    from chromadb.config import Settings
    from memory.episodic.episodic import EpisodicMemory
    from tests.test_episodic_chunks import _EF
    m = EpisodicMemory(storage_path=str(tmp_path / "src"))
    client = chromadb.PersistentClient(path=str(tmp_path / "src"), settings=Settings(anonymized_telemetry=False))
    m._collection = client.get_or_create_collection("episodic_memory", embedding_function=_EF())
    for i in range(6):
        asyncio.run(m.store("c", f"turul {i}", {"timestamp": float(i), "topic_id": "t1" if i != 3 else "t2"}))
    got = asyncio.run(m.neighbors("t1", 3, 2))  # sequence numbers 1..6; t1 except the 4th
    seqs = sorted(r["metadata"]["sequence_number"] for r in got)
    assert seqs == [1, 2, 3, 5] and all(r["metadata"]["topic_id"] == "t1" for r in got)


def test_layers_rerank_adds_windowed_neighbours_only_when_enabled(monkeypatch):
    import memory.config as cfg
    from memory.layers import MemoryLayers

    class _R:
        async def rerank(self, q, results):
            return [dict(r, blended_score=0.9) for r in results]

    class _E(_Ep):
        async def highlight(self, q, results):
            return [dict(r, content=r["content"] + "-chunk") for r in results]

        async def expand_chunks(self, results):
            return [dict(r, content=r["content"] + "+win") for r in results]

    layers = object.__new__(MemoryLayers)
    layers._reranker, layers._episodic = _R(), _E([_r("n11", 0, seq=11)])
    for k, v in (("CHUNKS_SEARCH", False), ("CHUNKS_HIGHLIGHTS", True), ("L3_WAVE2", True)):
        monkeypatch.setattr(cfg, k, v)
    out = asyncio.run(layers.rerank("q", [_r("s1", 0.0, seq=10)]))
    assert [r["metadata"]["message_id"] for r in out] == ["s1", "n11"]
    assert out[1]["content"] == "text n11-chunk+win" and out[1]["mechanisms"] == ["wave2"]
    monkeypatch.setattr(cfg, "L3_WAVE2", False)
    assert len(asyncio.run(layers.rerank("q", [_r("s1", 0.0, seq=10)]))) == 1


# ── 2026-11-07: neighbours must survive the retrieval cap ─────────────────────

from memory.retrieval import _cap_keep_neighbours  # noqa: E402


def _ranked40():
    return [_r(f"m{i}", 1.0 - i / 100, seq=10 * i) for i in range(40)]


def _with_neighbours():
    ranked = _ranked40()
    pool = [_r("n1", 0, seq=1), _r("n11", 0, seq=11), _r("n21", 0, seq=21)]  # next to m0, m1, m2
    return ranked, asyncio.run(add_neighbors(_Ep(pool), ranked, seeds=3, per_seed=2, window=2))


def test_old_positional_cut_dropped_every_neighbour():
    _, grown = _with_neighbours()
    assert len(grown) == 43 and not [r for r in grown[:20] if "wave2" in r.get("mechanisms", [])]


def test_cap_keeps_neighbours_of_surviving_seeds():
    _, grown = _with_neighbours()
    out = _cap_keep_neighbours(grown, 20)
    nb = [r for r in out if "wave2" in r.get("mechanisms", [])]
    assert {(r["metadata"]["message_id"], r["wave2_of"]) for r in nb} == {("n1", "m0"), ("n11", "m1"), ("n21", "m2")}


def test_cap_never_reorders_or_rescores_the_original_results():
    ranked, grown = _with_neighbours()
    out = _cap_keep_neighbours(grown, 20)
    base = [r for r in out if "wave2" not in r.get("mechanisms", [])]
    assert base == ranked[:20]
    assert [r["blended_score"] for r in base] == [r["blended_score"] for r in ranked[:20]]


def test_neighbour_of_a_seed_below_the_cap_is_dropped():
    ranked = _ranked40()
    orphan = _r("x", 0.1, seq=999, mechanisms=["wave2"], wave2_of="m20")  # m20 = 21st, cut
    out = _cap_keep_neighbours(ranked + [orphan], 20)
    assert out == ranked[:20]


def test_short_lists_are_returned_unchanged():
    ranked = [_r("a", 0.9), _r("b", 0.8, mechanisms=["wave2"], wave2_of="a")]
    assert _cap_keep_neighbours(ranked, 20) is ranked


def test_e2e_neighbours_reach_the_l3_block_and_seed_order_is_unchanged():
    from memory.context_assembler import assemble_blocks
    _, grown = _with_neighbours()
    for r in grown:
        r["metadata"]["timestamp"] = 1_700_000_000
    out = _cap_keep_neighbours(grown, 20)
    prov: dict = {}
    blocks, _ = assemble_blocks(100_000, out, {}, "id", [], 3.0, None, prov)
    old_blocks, _ = assemble_blocks(100_000, grown[:20], {}, "id", [], 3.0, None)
    assert prov["wave2_ranks"]
    new_l3 = [ln for ln in blocks[-1].split("\n")[1:] if "text n" not in ln]
    assert new_l3 == old_blocks[-1].split("\n")[1:]  # original entries: same ones, same order
