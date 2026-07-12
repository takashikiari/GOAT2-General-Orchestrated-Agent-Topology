"""tests.test_result_merger — RRF fusion + mechanism attribution (spec §10.2).

merge_results now takes labeled groups (mechanism_name, results) instead of
bare result lists, and tags each merged result with every mechanism that
contributed to it — needed so a benchmark can report hit@K per mechanism
without re-deriving provenance by hand.
"""
from __future__ import annotations

from memory.result_merger import _result_id, merge_results


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


def test_bm25_dedup_requires_message_id_in_metadata():
    """Documents the dedup identity bug and its fix contract (memory/result_merger.py
    ``_result_id``). ``EpisodicMemory.store`` stamps ``message_id`` on its own
    internal metadata copy right before writing to Chroma; that stamp never
    propagated back to the dict ``store_episodic`` spread into
    ``BM25Index.add_doc``, so a BM25-recovered hit for the SAME document had no
    ``message_id`` and resolved to a different dedup key (raw content string)
    than the semantic-sourced hit (which always has ``message_id``). RRF then
    fused the same document as two separate candidates, wasting a merge-pool
    slot. After the fix, BM25's cached metadata also carries ``message_id`` so
    both mechanisms' dicts resolve to the same identity and get fused into one.
    """
    doc_id = "doc-abc"
    content = "GOAT is a memory system"
    semantic_result = {"metadata": {"message_id": doc_id}, "content": content}

    # Pre-fix shape: BM25's cached metadata lacked message_id.
    buggy_bm25_result = {"metadata": {"chat_id": "c1"}, "content": content}
    assert _result_id(semantic_result) != _result_id(buggy_bm25_result)
    buggy_merged = merge_results([
        ("semantic_global", [semantic_result]),
        ("bm25", [buggy_bm25_result]),
    ])
    assert len(buggy_merged) == 2  # false duplicate: same doc counted twice

    # Post-fix shape: BM25's cached metadata carries message_id.
    fixed_bm25_result = {"metadata": {"chat_id": "c1", "message_id": doc_id}, "content": content}
    assert _result_id(semantic_result) == _result_id(fixed_bm25_result) == doc_id
    fixed_merged = merge_results([
        ("semantic_global", [semantic_result]),
        ("bm25", [fixed_bm25_result]),
    ])
    assert len(fixed_merged) == 1
    assert fixed_merged[0]["mechanisms"] == ["bm25", "semantic_global"]
