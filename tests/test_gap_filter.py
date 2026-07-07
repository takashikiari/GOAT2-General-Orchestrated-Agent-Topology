"""tests.test_gap_filter — unit tests for gap_filter.

Pure function: no I/O, no mocks needed. Each case exercises a distinct branch
or edge condition of the significance-ratio logic.
"""
from __future__ import annotations

from memory.context_assembler import gap_filter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _r(score: float) -> dict:
    return {"score": score, "content": "x", "metadata": {"timestamp": 0.0}}


def _scores(results: list[dict]) -> list[float]:
    return [r["score"] for r in results]


# ---------------------------------------------------------------------------
# Edge cases: too few results to compute a ratio
# ---------------------------------------------------------------------------

def test_empty_returns_empty():
    assert gap_filter([]) == []


def test_single_result_returned():
    r = _r(0.5)
    assert gap_filter([r]) == [r]


def test_two_results_both_returned():
    """With exactly 2 results there is only 1 gap: max_gap == mean_gap, so
    ratio == 1.0 < significance=3.0. The function must NOT return [] here —
    that would silently discard potentially relevant results. Instead it
    returns all results (< 3 items means the ratio criterion is meaningless)."""
    r1, r2 = _r(0.3), _r(0.9)
    result = gap_filter([r1, r2])
    assert result == [r1, r2]


def test_two_results_far_result_excluded_by_ceiling():
    """With 2 results, the ratio is always 1.0 so the gap criterion can't run.
    The fallback ceiling (1.5) still excludes the clearly-irrelevant result at
    2.0 even though no structural gap test is possible."""
    r1, r2 = _r(0.1), _r(2.0)
    assert gap_filter([r1, r2]) == [r1]


def test_single_result_above_ceiling_excluded():
    """A single result scoring above 1.5 is still excluded — the ceiling applies
    to all len < 3 paths, not just the 2-result case."""
    assert gap_filter([_r(2.0)]) == []


def test_two_results_irrelevant_second_excluded():
    """Concrete case from V3 empirical run (2-doc tmp corpus):
    'who is Takashi' → doc A (Takashi bio, 0.798) + doc B ('salut buna ziua', 1.760).
    Doc B exceeds the 1.5 ceiling and must be excluded despite no gap ratio being
    computable."""
    r_relevant = _r(0.798)
    r_noise = _r(1.760)
    result = gap_filter([r_relevant, r_noise])
    assert result == [r_relevant]


# ---------------------------------------------------------------------------
# Structural gap cases: ratio > significance → cut
# ---------------------------------------------------------------------------

def test_clear_cluster_followed_by_outlier():
    """Tight cluster [0.30, 0.35, 0.40, 0.45] + outlier [2.0].
    gaps = [0.05, 0.05, 0.05, 1.55], mean = 0.425, ratio = 3.65 > 3.0 → keep 4."""
    results = [_r(s) for s in [0.30, 0.35, 0.40, 0.45, 2.0]]
    filtered = gap_filter(results)
    assert _scores(filtered) == [0.30, 0.35, 0.40, 0.45]


def test_gap_at_first_position_keeps_one():
    """Large gap right after the best result: only result[0] is kept."""
    # [0.2, 1.5, 1.55, 1.6, 1.65]: gaps=[1.3, 0.05, 0.05, 0.05]
    # mean=0.3625, ratio=1.3/0.3625=3.59 > 3.0 → cut@0, keeps [0.2]
    results = [_r(s) for s in [0.2, 1.5, 1.55, 1.6, 1.65]]
    filtered = gap_filter(results)
    assert _scores(filtered) == [0.2]


def test_archive_cluster():
    """Simulates a corpus flooded with l2_full_archive docs for a repeated query.
    Very tight low-distance cluster [0.02, 0.05, 0.08] then jump to noise [1.4, 1.5].
    gaps=[0.03, 0.03, 1.32, 0.10], mean=0.37, ratio=1.32/0.37=3.57 > 3.0 → keep 3."""
    results = [_r(s) for s in [0.02, 0.05, 0.08, 1.4, 1.5]]
    filtered = gap_filter(results)
    assert _scores(filtered) == [0.02, 0.05, 0.08]


def test_custom_significance_stricter():
    """significance=5.0 rejects a gap that significance=3.0 would accept."""
    # [0.3, 0.35, 0.4, 0.45, 2.0]: ratio=3.65 — passes 3.0 but not 5.0
    results = [_r(s) for s in [0.30, 0.35, 0.40, 0.45, 2.0]]
    assert gap_filter(results, significance=5.0) == []


def test_custom_significance_looser():
    """significance=1.5 accepts a gap that significance=3.0 rejects."""
    # [0.3, 0.35, 0.4, 0.9]: gaps=[0.05, 0.05, 0.5], mean=0.2, ratio=0.5/0.2=2.5
    # passes 1.5 but not 3.0
    results = [_r(s) for s in [0.3, 0.35, 0.4, 0.9]]
    assert gap_filter(results, significance=3.0) == []
    filtered = gap_filter(results, significance=1.5)
    assert _scores(filtered) == [0.3, 0.35, 0.4]


# ---------------------------------------------------------------------------
# Uniform distribution → no structural break → []
# ---------------------------------------------------------------------------

def test_uniform_scores_returns_empty():
    """Equally spaced scores → all gaps identical → ratio=1.0 < 3.0 → []."""
    results = [_r(s) for s in [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]]
    assert gap_filter(results) == []


def test_identical_scores_returns_empty():
    """All scores equal → all gaps=0 → mean_gap=0 → early exit returns []."""
    results = [_r(1.3) for _ in range(5)]
    assert gap_filter(results) == []


def test_monothematic_corpus_unrelated_query():
    """Representative unrelated query against a monothematic corpus:
    scores cluster between 1.33–1.51 with no strong structural break.
    Simulates 'salut azi' distribution from V3 calibration run.
    gaps roughly uniform → ratio < 3.0 → nothing injected."""
    results = [_r(s) for s in [1.331, 1.342, 1.379, 1.432, 1.448, 1.473, 1.486, 1.505]]
    assert gap_filter(results) == []
