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
    _tokens,
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


# --- _tokens / Romanian stopwords (bug 1a, 2026-07-12) -----------------------

def test_tokens_strips_common_function_words():
    toks = _tokens("Pai și când am vorbit noi de site?")
    assert "și" not in toks
    assert "am" not in toks
    assert "de" not in toks
    assert "noi" not in toks
    # content words survive
    assert "vorbit" in toks
    assert "site" in toks


def test_tokens_keeps_question_words_that_anchor_topic():
    # "ce" ("what") is deliberately NOT a stopword — it can anchor a topic.
    toks = _tokens("Verifică ce data e azi")
    assert "ce" in toks
    assert "data" in toks
    assert "azi" in toks
    # "e" (a stopword form of "is") is stripped.
    assert "e" not in toks


def test_lexical_overlap_stopwords_do_not_count_as_shared_content():
    # Old behaviour: "de"/"pe" shared inflated overlap even with no shared
    # content words. New behaviour: only "parola"/"wifi" can count.
    overlap = lexical_overlap("care e parola de wifi", ["nu mai am parola de acasa"])
    # shared content: {parola} ; union content: {parola, wifi, acasa} (stopwords removed)
    assert abs(overlap - (1 / 3)) < 1e-9


# --- classify_turn: real production pairs (2026-07-12 recalibration) ---------
#
# Measured against the live production MiniLM model on real chat 1912576407
# consecutive turn pairs (2026-07-12 window) — hardcoded here as fixtures
# (not re-embedded at test time) so the test stays fast/deterministic. Every
# pair below is a turn a human would call "same thread"; under the OLD
# thresholds (drift_cold=0.55, lexical_low=0.15, no stopword filtering) three
# of the four hard-reset to "cold" (warm fired 0/30 times in the real window).
# lex values are recomputed WITH the 2026-07-12 stopword filter applied (see
# _STOPWORDS_RO / _tokens) — all land at 0.0 for these short exchanges since
# their few shared tokens ("de", "pe", "nu", "ce") are themselves stopwords;
# see config/memory.toml [activation] for the full calibration rationale
# (drift_cold 0.55->0.30, lexical_low 0.15->0.10).
_REAL_PAIRS_2026_07_12 = [
    ("Verifică ce data e azi", "Pai și când am vorbit noi de site?", 0.609),
    ("Pai și când am vorbit noi de site? Pe ce data?", "Nu azi, înainte de sesiunea de azi", 0.344),
    ("Nu azi, înainte de sesiunea de azi, pe 9 iulie, nu?", "Din ce cauza ai încurcat cronologia?", 0.471),
    ("Din ce cauza ai încurcat cronologia?", "Cum rezolvam problema asta?", 0.504),
]


def test_real_pairs_lexical_overlap_is_zero_with_stopwords_removed():
    """Ground the fixture cosines above: these short follow-ups share no
    CONTENT tokens once stopwords are removed (the reason recalibration had
    to lean on the cosine threshold, not lexical_low, for these specific
    pairs)."""
    for prev, query, _cos in _REAL_PAIRS_2026_07_12:
        assert lexical_overlap(query, [prev]) == 0.0


def test_real_pairs_no_longer_hard_reset_to_cold():
    """The core bug-1 regression test: at the OLD thresholds 3/4 of these
    genuinely-continuous pairs were force-reset to "cold" (losing the thread
    entirely). At the recalibrated thresholds none of them do — they land on
    "drift" (a targeted refresh, not a full reset) since none reach the warm
    bar either."""
    for prev, query, cos in _REAL_PAIRS_2026_07_12:
        act = Activation(centroid=[1.0, 0.0], recent_queries=[prev])
        # Project a 2D embedding at the measured cosine to the centroid [1,0]:
        # cosine([1,0], [cos, sqrt(1-cos^2)]) == cos.
        query_emb = [cos, (1 - cos * cos) ** 0.5]
        state = classify_turn(query, act, query_emb)
        assert state != "cold", f"pair cos={cos} wrongly hard-reset to cold: {prev!r} -> {query!r}"


def test_recalibrated_drift_cold_below_all_real_pair_cosines():
    from memory.config import ACTIVATION_DRIFT_COLD, ACTIVATION_LEXICAL_LOW
    assert ACTIVATION_DRIFT_COLD < min(cos for _, _, cos in _REAL_PAIRS_2026_07_12)
    assert ACTIVATION_DRIFT_COLD == 0.30
    assert ACTIVATION_LEXICAL_LOW == 0.10


def test_classify_turn_still_colds_a_clean_orthogonal_switch():
    # Sanity check that recalibration didn't disable "cold" outright: a
    # genuinely orthogonal embedding (cosine 0.0) with zero lexical overlap
    # must still hard-reset.
    act = Activation(centroid=[1.0, 0.0], recent_queries=["parola de wifi"])
    assert classify_turn("reteta de ciorba", act, [0.0, 1.0]) == "cold"


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


def test_rescore_recency_uses_configured_blend_weights(monkeypatch):
    """Bug 3 (2026-07-12): the 0.7/0.3 blend was hardcoded in the function
    body; it now reads PREFETCH_RECENCY_BASE_WEIGHT/RECENCY_WEIGHT from
    memory.activation's module namespace (config-driven, like every other
    [prefetch] tunable). Monkeypatching those constants must change the
    computed blended_score."""
    import memory.activation as activation_mod

    r = _result("x", ts=0.0)  # ts=0 -> recency fraction fully attenuated to 0.0
    r["blended_score"] = 0.8

    monkeypatch.setattr(activation_mod, "PREFETCH_RECENCY_BASE_WEIGHT", 1.0)
    monkeypatch.setattr(activation_mod, "PREFETCH_RECENCY_RECENCY_WEIGHT", 0.0)
    out_all_base = activation_mod.rescore_recency([dict(r)], now=10_000_000.0)
    assert abs(out_all_base[0]["blended_score"] - 0.8) < 1e-9  # pure base score, no recency term

    monkeypatch.setattr(activation_mod, "PREFETCH_RECENCY_BASE_WEIGHT", 0.0)
    monkeypatch.setattr(activation_mod, "PREFETCH_RECENCY_RECENCY_WEIGHT", 1.0)
    out_all_recency = activation_mod.rescore_recency([dict(r)], now=10_000_000.0)
    assert abs(out_all_recency[0]["blended_score"] - 0.0) < 1e-9  # pure recency term, fully attenuated


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