"""tests.test_activation — unit tests for the L2.5 activation pure logic.

No services: ``classify_turn`` / ``classify_write`` / ``cosine`` /
``lexical_overlap`` / ``rescore_recency`` / ``trim_recent`` take plain values, so
they test the brain-activation decisions in isolation. The turn-state machine
(cold / warm / drift) and the enriching/filing write split are the seams the
whole design lives or dies on, so each branch is pinned here.
"""
from __future__ import annotations

import time

from memory.activation import (
    Activation,
    classify_turn,
    classify_write,
    cosine,
    lexical_overlap,
    rescore_recency,
    trim_recent,
)

# --- cosine ------------------------------------------------------------------

def test_cosine_identical_is_one():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_cosine_orthogonal_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_known_value():
    # cosine([1,0], [0.8,0.6]) == 0.8
    assert abs(cosine([1.0, 0.0], [0.8, 0.6]) - 0.8) < 1e-9


def test_cosine_none_safe():
    assert cosine(None, [1.0]) == 0.0
    assert cosine([1.0], None) == 0.0


def test_cosine_empty_or_mismatched_length_is_zero():
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 2.0], [1.0]) == 0.0


# --- lexical_overlap ---------------------------------------------------------

def test_lexical_overlap_no_recent_is_zero():
    assert lexical_overlap("what is x", []) == 0.0


def test_lexical_overlap_shared_tokens():
    # query {wifi, password, email} ∩ recent {wifi, password, coffee} = {wifi, password} (2)
    # union {wifi, password, email, coffee} (4) → Jaccard 2/4 = 0.5
    assert abs(lexical_overlap("wifi password email", ["wifi password coffee"]) - 0.5) < 1e-9


def test_lexical_overlap_disjoint_is_zero():
    assert lexical_overlap("alpha beta", ["gamma delta"]) == 0.0


def test_lexical_overlap_punctuation_and_case_insensitive():
    # "Wifi!" and "wifi" are the same token after strip + lower.
    assert lexical_overlap("Wifi!", ["wifi"]) == 1.0


# --- classify_turn -----------------------------------------------------------

def _activation(centroid, recent):
    return Activation(centroid=centroid, recent_queries=recent)


def test_classify_turn_cold_when_no_activation():
    assert classify_turn("anything", None, [1.0, 0.0]) == "cold"


def test_classify_turn_cold_when_no_embedding():
    act = _activation([1.0, 0.0], ["wifi password"])
    assert classify_turn("wifi password", act, None) == "cold"


def test_classify_turn_cold_when_centroid_empty():
    act = _activation([], ["x"])
    assert classify_turn("x", act, [1.0]) == "cold"


def test_classify_turn_warm_on_high_similarity():
    # identical vectors → cosine 1.0 ≥ drift_warm(0.92) → warm
    act = _activation([1.0, 0.0], ["what is my wifi password"])
    assert classify_turn("what is my wifi password", act, [1.0, 0.0]) == "warm"


def test_classify_turn_drift_in_middle_band():
    # cosine 0.6 ∈ (cold=0.55, warm=0.80) → drift (the tuned middle band)
    act = _activation([1.0, 0.0], ["something"])
    assert classify_turn("moved a bit", act, [0.6, 0.8]) == "drift"


def test_classify_turn_cold_on_consensus_shift():
    # cosine 0.0 (< cold) AND lexical 0.0 (< low) → consensus shift → cold
    act = _activation([1.0, 0.0], ["alpha beta"])
    assert classify_turn("gamma delta", act, [0.0, 1.0]) == "cold"


def test_classify_turn_drift_not_cold_when_only_one_signal_drops():
    # cosine 0.0 (< cold) but lexical 0.5 (≥ low): NOT a consensus shift → drift.
    # A short follow-up that drifts in embedding but keeps lexical overlap must
    # not falsely reset the thread (the false-break / flicker protection).
    act = _activation([1.0, 0.0], ["wifi password coffee"])
    assert classify_turn("wifi password email", act, [0.0, 1.0]) == "drift"


# --- classify_write ----------------------------------------------------------

def test_classify_write_enriching_when_close_to_centroid():
    # identical → cosine 1.0 ≥ enriching_sim(0.55) → enriching
    assert classify_write([1.0, 0.0], [1.0, 0.0]) == "enriching"


def test_classify_write_filing_when_far_from_centroid():
    # orthogonal → cosine 0.0 < 0.55 → filing
    assert classify_write([0.0, 1.0], [1.0, 0.0]) == "filing"


def test_classify_write_filing_when_no_activation_or_embedding():
    assert classify_write(None, [1.0, 0.0]) == "filing"
    assert classify_write([1.0, 0.0], None) == "filing"
    assert classify_write([1.0, 0.0], []) == "filing"


# --- rescore_recency ---------------------------------------------------------

def _result(content, ts, distance=0.5):
    return {"content": content, "metadata": {"timestamp": ts, "access_count": 0}, "score": distance}


def test_rescore_recency_newer_scores_higher_and_sorts_first():
    now = 100_000.0
    old = _result("old", now - 10_000)
    new = _result("new", now - 10)
    out = rescore_recency([old, new], now)
    # the newer result sorts first (its recency term is larger)
    assert out[0]["content"] == "new"
    assert out[0]["blended_score"] > out[1]["blended_score"]


def test_rescore_recency_attenuates_with_time():
    """The same held result scores lower as `now` advances (time attenuates)."""
    r = _result("x", 0.0, distance=0.5)
    early = rescore_recency([r], 100.0)[0]["blended_score"]
    late = rescore_recency([r], 1_000_000.0)[0]["blended_score"]
    assert early > late


# --- trim_recent -------------------------------------------------------------

def test_trim_recent_appends_within_window():
    assert trim_recent(["a", "b"], "c") == ["a", "b", "c"]


def test_trim_recent_caps_at_window():
    # window is 5 → the oldest drops when a 6th is appended
    assert trim_recent(["a", "b", "c", "d", "e"], "f") == ["b", "c", "d", "e", "f"]


from memory.activation import (
    update_centroid_weighted,
    find_topic_return,
    archive_current_topic,
)


# --- update_centroid_weighted -----------------------------------------------

def test_update_centroid_weighted_full_replace_at_turn_one():
    # turn_count=1 → alpha = 1/min(1,20) = 1.0 → result is pure query_emb
    result = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 1)
    assert result == [0.0, 1.0]


def test_update_centroid_weighted_blend_at_turn_two():
    # turn_count=2 → alpha = 0.5 → 50/50 blend
    result = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 2)
    assert abs(result[0] - 0.5) < 1e-9
    assert abs(result[1] - 0.5) < 1e-9


def test_update_centroid_weighted_stable_at_high_turn_count():
    # turn_count=20 → alpha = 1/20 = 0.05 → 95% centroid + 5% query
    result = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 20)
    assert abs(result[0] - 0.95) < 1e-9
    assert abs(result[1] - 0.05) < 1e-9


def test_update_centroid_weighted_caps_alpha_at_twenty():
    # turn_count=50 → min(50, 20) = 20 → same as turn_count=20
    r20 = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 20)
    r50 = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 50)
    assert r20 == r50


# --- find_topic_return -------------------------------------------------------

def test_find_topic_return_matches_closest_above_threshold():
    archived = [
        {"topic_id": "t1", "centroid": [1.0, 0.0], "ts": 1.0},
        {"topic_id": "t2", "centroid": [0.0, 1.0], "ts": 2.0},
    ]
    result = find_topic_return([1.0, 0.0], archived, threshold=0.75)
    assert result == "t1"


def test_find_topic_return_none_when_below_threshold():
    # cosine([0.6, 0.8], [1.0, 0.0]) = 0.6 < 0.75
    archived = [{"topic_id": "t1", "centroid": [1.0, 0.0], "ts": 1.0}]
    result = find_topic_return([0.6, 0.8], archived, threshold=0.75)
    assert result is None


def test_find_topic_return_none_on_empty_inputs():
    assert find_topic_return(None, [{"topic_id": "t1", "centroid": [1.0, 0.0], "ts": 1.0}], 0.75) is None
    assert find_topic_return([1.0, 0.0], [], 0.75) is None


# --- archive_current_topic ---------------------------------------------------

def test_archive_current_topic_appends_entry():
    act = Activation(centroid=[1.0, 0.0], topic_id="t1", ts=1.0, archived_topics=[])
    result = archive_current_topic(act, max_archived=10)
    assert len(result) == 1
    assert result[0]["topic_id"] == "t1"
    assert result[0]["centroid"] == [1.0, 0.0]


def test_archive_current_topic_trims_to_max_and_drops_oldest():
    existing = [{"topic_id": f"t{i}", "centroid": [float(i), 0.0], "ts": float(i)} for i in range(10)]
    act = Activation(centroid=[10.0, 0.0], topic_id="t10", ts=10.0, archived_topics=existing)
    result = archive_current_topic(act, max_archived=10)
    assert len(result) == 10
    assert result[-1]["topic_id"] == "t10"
    assert result[0]["topic_id"] == "t1"   # t0 dropped


def test_archive_current_topic_deduplicates_same_topic_id():
    existing = [{"topic_id": "t1", "centroid": [0.5, 0.5], "ts": 1.0}]
    act = Activation(centroid=[1.0, 0.0], topic_id="t1", ts=2.0, archived_topics=existing)
    result = archive_current_topic(act, max_archived=10)
    t1_entries = [e for e in result if e["topic_id"] == "t1"]
    assert len(t1_entries) == 1
    assert t1_entries[0]["ts"] == 2.0


def test_archive_current_topic_noop_when_no_topic_id():
    act = Activation(centroid=[1.0, 0.0], topic_id="", ts=1.0, archived_topics=[])
    result = archive_current_topic(act, max_archived=10)
    assert result == []


def test_activation_roundtrip_preserves_new_fields():
    act = Activation(
        centroid=[1.0, 0.0], merged=[], last_query="q", recent_queries=["q"],
        ts=1.0, topic_id="abc-123", turn_count=5,
        archived_topics=[{"topic_id": "old", "centroid": [0.0, 1.0], "ts": 0.5}],
    )
    restored = Activation.from_dict(act.to_dict())
    assert restored.topic_id == "abc-123"
    assert restored.turn_count == 5
    assert len(restored.archived_topics) == 1
    assert restored.archived_topics[0]["topic_id"] == "old"


def test_activation_from_dict_defaults_new_fields_when_missing():
    # Old Redis blob — no topic fields. Must deserialise safely.
    old_blob = {"centroid": [1.0, 0.0], "merged": [], "last_query": "q",
                "recent_queries": [], "ts": 1.0}
    act = Activation.from_dict(old_blob)
    assert act.topic_id == ""
    assert act.turn_count == 0
    assert act.archived_topics == []