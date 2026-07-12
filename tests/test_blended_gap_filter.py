"""tests.test_blended_gap_filter — unit tests for blended_gap_filter.

Pure function: no I/O, no mocks. Tests cover the structural-gap branch,
the minimum-score fallback (_BLENDED_MIN_SCORE = 0.25), and edge cases.
"""
from __future__ import annotations

from memory.context_assembler import blended_gap_filter, _BLENDED_MIN_SCORE


def _r(blended: float) -> dict:
    return {"blended_score": blended, "content": "x", "metadata": {"timestamp": 0.0}}


def _scores(results: list[dict]) -> list[float]:
    return [r["blended_score"] for r in results]


# --- edge cases ----------------------------------------------------------------

def test_empty_returns_empty():
    assert blended_gap_filter([]) == []


def test_single_above_min_returned():
    r = _r(0.5)
    assert blended_gap_filter([r]) == [r]


def test_single_below_min_excluded():
    assert blended_gap_filter([_r(0.2)]) == []


def test_two_results_min_floor_applied():
    r_good, r_bad = _r(0.6), _r(0.2)
    result = blended_gap_filter([r_good, r_bad])
    assert result == [r_good]


# --- structural gap: large cluster separation ----------------------------------

def test_clear_cluster_with_gap():
    """Tight cluster [0.82,0.79,0.78] then drop to [0.31,0.29].
    gaps=[0.03,0.01,0.47,0.02], mean=0.1325, ratio=0.47/0.1325=3.54 >3 → keep 3."""
    results = [_r(s) for s in [0.82, 0.79, 0.78, 0.31, 0.29]]
    filtered = blended_gap_filter(results)
    assert _scores(filtered) == [0.82, 0.79, 0.78]


def test_gap_at_first_position():
    """Large gap right after top result → only top result kept."""
    results = [_r(s) for s in [0.90, 0.30, 0.28, 0.27, 0.25]]
    # gaps=[0.60,0.02,0.01,0.02], mean=0.1625, ratio=0.60/0.1625=3.69 >3 → keep 1
    filtered = blended_gap_filter(results)
    assert _scores(filtered) == [0.90]


# --- uniform distribution → minimum-score fallback ----------------------------

def test_uniform_applies_min_floor():
    """No structural gap → fallback to _BLENDED_MIN_SCORE cutoff."""
    results = [_r(s) for s in [0.55, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20]]
    filtered = blended_gap_filter(results)
    # Only those >= _BLENDED_MIN_SCORE (0.25) survive
    assert all(r["blended_score"] >= _BLENDED_MIN_SCORE for r in filtered)
    assert _scores(filtered) == [0.55, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25]


def test_all_below_min_floor_returns_empty():
    """All results below minimum → nothing injected."""
    results = [_r(s) for s in [0.24, 0.20, 0.15, 0.10]]
    assert blended_gap_filter(results) == []


def test_all_above_min_no_gap_all_kept():
    """All results above min with no structural gap → all kept."""
    results = [_r(s) for s in [0.80, 0.78, 0.76, 0.74, 0.72]]
    filtered = blended_gap_filter(results)
    assert len(filtered) == 5


# --- temporal-matched results are protected from the gap cut -------------------

def _rm(blended: float, mechanisms: tuple = ()) -> dict:
    return {
        "blended_score": blended, "content": "x",
        "metadata": {"timestamp": 0.0}, "mechanisms": list(mechanisms),
    }


def test_temporal_result_survives_dramatic_top_gap():
    """A result matched by the user's explicit date/time reference (mechanisms
    includes 'temporal') must never be dropped by the generic gap-cut, even
    when a same-day/self-referential result dominates the score distribution.

    Confirmed against real production data (2026-07-12): a query naming an
    explicit past date got its target memory reranked below same-session
    noise, then gap-filtered down to a single unrelated result — the window
    the user explicitly asked for is a relevance signal gap_filter has no
    way to see from blended_score alone.
    """
    results = [
        _rm(0.90),                        # dominant, non-temporal — wins alone normally
        _rm(0.30, mechanisms=["temporal"]),  # what the user actually asked for
        _rm(0.28),
        _rm(0.27),
        _rm(0.25),
    ]
    filtered = blended_gap_filter(results)
    assert results[1] in filtered
    assert filtered[0] is results[0]  # non-temporal behavior unchanged: dominant result still wins first


def test_temporal_results_merged_in_score_order():
    """Rescued temporal results are merged back in blended_score-descending
    order — fit_search_results (downstream) assumes best-first — not just
    appended at the end regardless of score."""
    results = [
        _rm(0.90),
        _rm(0.30, mechanisms=["temporal"]),
        _rm(0.10),
        _rm(0.05, mechanisms=["temporal"]),
    ]
    filtered = blended_gap_filter(results)
    scores = [r["blended_score"] for r in filtered]
    assert scores == sorted(scores, reverse=True)


def test_no_temporal_mechanism_behavior_unchanged():
    """Without any 'temporal' tag, the dramatic-top-gap collapse-to-1 behavior
    (test_gap_at_first_position) is untouched by this change."""
    results = [_r(s) for s in [0.90, 0.30, 0.28, 0.27, 0.25]]
    assert blended_gap_filter(results) == [results[0]]
