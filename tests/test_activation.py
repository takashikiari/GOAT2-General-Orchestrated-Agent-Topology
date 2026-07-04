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