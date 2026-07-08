"""tests.test_result_merger — RRF fusion + mechanism attribution (spec §10.2).

merge_results now takes labeled groups (mechanism_name, results) instead of
bare result lists, and tags each merged result with every mechanism that
contributed to it — needed so a benchmark can report hit@K per mechanism
without re-deriving provenance by hand.
"""
from __future__ import annotations

from memory.result_merger import merge_results


def _r(message_id: str, content: str = "") -> dict:
    return {"metadata": {"message_id": message_id}, "content": content or message_id}


def test_single_mechanism_tags_all_results():
    merged = merge_results([("bm25", [_r("a"), _r("b")])])
    assert {r["metadata"]["message_id"]: r["mechanisms"] for r in merged} == {
        "a": ["bm25"], "b": ["bm25"],
    }


def test_result_in_multiple_groups_gets_union_of_mechanisms():
    merged = merge_results([
        ("semantic_global", [_r("a"), _r("b")]),
        ("bm25", [_r("b"), _r("c")]),
    ])
    by_id = {r["metadata"]["message_id"]: r["mechanisms"] for r in merged}
    assert by_id["a"] == ["semantic_global"]
    assert by_id["b"] == ["bm25", "semantic_global"]          # sorted, both mechanisms
    assert by_id["c"] == ["bm25"]


def test_rrf_ranking_unaffected_by_mechanism_tagging():
    """A doc found by two mechanisms outranks one found by only one, same best rank."""
    merged = merge_results([
        ("semantic_global", [_r("a")]),     # a: rank1 -> 1/61
        ("bm25", [_r("a")]),                # a: rank1 -> 1/61 (total 2/61)
        ("temporal", [_r("c")]),            # c: rank1 -> 1/61 (total 1/61)
    ])
    ids = [r["metadata"]["message_id"] for r in merged]
    assert ids[0] == "a"
    assert ids[1] == "c"
    assert merged[0]["blended_score"] > merged[1]["blended_score"]
    assert "blended_score" in merged[0]


def test_empty_groups_returns_empty_list():
    assert merge_results([]) == []
    assert merge_results([("bm25", [])]) == []
