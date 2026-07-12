"""tests.test_fit_search_results — unit tests for fit_search_results.

Pure function: no I/O, no mocks. Covers the base closest-first packing
behaviour plus the temporal-tagged protected-budget fix (bug 1: a result
rescued by blended_gap_filter's temporal carve-out, then re-sorted to the
back of the list by blended_score, must not be silently dropped again by
fit_search_results just because it now sits near the end of `results`).
"""
from __future__ import annotations

from memory.context_assembler import fit_search_results


def _r(content: str, mechanisms: tuple = (), ts: float = 0.0) -> dict:
    return {
        "content": content,
        "metadata": {"timestamp": ts},
        "mechanisms": list(mechanisms),
    }


# --- base behaviour (no temporal tags) ------------------------------------------

def test_empty_results_returns_empty_block():
    block, count = fit_search_results([], 100)
    assert block == "" and count == 0


def test_all_results_fit_within_budget():
    results = [_r("a"), _r("b"), _r("c")]
    block, count = fit_search_results(results, 1000)
    assert count == 3
    assert "a" in block and "b" in block and "c" in block


def test_packs_closest_first_until_budget_exhausted():
    """Order-preserving packing when nothing is temporal-tagged (existing behaviour)."""
    results = [_r("x" * 40), _r("y" * 40), _r("z" * 40)]
    # Each line is ~ "- " + 40 chars = ~10-11 tokens. Budget for ~1.5 items.
    block, count = fit_search_results(results, 12)
    assert count == 1
    assert "x" * 40 in block


# --- bug 1: temporal-tagged results get a protected budget share ---------------

def test_temporal_result_survives_tight_budget_even_when_sorted_last():
    """Mirrors production: blended_gap_filter re-sorts kept+rescued by score
    descending, so a low-score rescued temporal result ends up near the END
    of the list passed to fit_search_results. A tight budget that would only
    fit the high-score non-temporal items (in list order) must still make
    room for the temporal one.
    """
    results = [
        _r("high score non-temporal A" * 3),
        _r("high score non-temporal B" * 3),
        _r("high score non-temporal C" * 3),
        _r("the target the user asked for", mechanisms=["temporal"]),  # last, low score
    ]
    # Budget too tight for all 4 in list order, but large enough for the
    # temporal one plus a couple of the others once it's prioritized.
    budget = sum(len(f"- {r['content']}") // 4 for r in results[:2]) + 5
    block, count = fit_search_results(results, budget)
    assert "the target the user asked for" in block


def test_temporal_results_packed_before_non_temporal_in_chronological_order():
    """Multiple temporal results: packed before any non-temporal result, in
    chronological (oldest-first) order among themselves — not blended_score
    order. Date-window membership is the relevance signal for these results;
    ranking them by score against the query text is what let same-day
    self-referential noise crowd out a genuinely-in-window result in
    production (confirmed live 2026-07-12: 'azi pe la 12:00' packed the
    CURRENT turn's own echoed question and other same-day meta-conversation
    ahead of a real exchange that happened at the actually-requested time,
    purely because they scored higher on textual similarity to the query)."""
    results = [
        _r("non-temporal high"),
        _r("temporal newer", mechanisms=["temporal"], ts=200.0),
        _r("temporal older", mechanisms=["temporal"], ts=100.0),
    ]
    block, count = fit_search_results(results, 1000)
    lines = block.split("\n")
    assert lines[0].endswith("temporal older")
    assert lines[1].endswith("temporal newer")
    assert lines[2].endswith("non-temporal high")


def test_temporal_results_with_tied_timestamps_keep_relative_order():
    """Equal timestamps (e.g. synthetic fixtures, or same-second writes) fall
    back to stable-sort behaviour — original relative order preserved, not
    reshuffled arbitrarily."""
    results = [
        _r("non-temporal high"),
        _r("temporal first", mechanisms=["temporal"]),
        _r("temporal second", mechanisms=["temporal"]),
    ]
    block, count = fit_search_results(results, 1000)
    lines = block.split("\n")
    assert lines[0].endswith("temporal first")
    assert lines[1].endswith("temporal second")
    assert lines[2].endswith("non-temporal high")


def test_temporal_results_still_truncate_when_too_many_to_fit():
    """Protected status is not unlimited: with many temporal results and a
    tight budget, only as many as fit are kept, still best-first among
    themselves."""
    results = [
        _r("temporal one", mechanisms=["temporal"]),
        _r("temporal two", mechanisms=["temporal"]),
        _r("temporal three", mechanisms=["temporal"]),
    ]
    one_line_budget = (len("- temporal one") // 4) + 1
    block, count = fit_search_results(results, one_line_budget)
    assert count == 1
    assert "temporal one" in block


# --- temporal_center: proximity-to-requested-moment ordering -------------------

def test_temporal_center_orders_by_proximity_not_chronology():
    """When the caller knows the exact moment the user asked about (parsed
    from the query, e.g. 'azi pe la 12:00' -> center=noon), order the
    temporal group by closeness to THAT moment, not simple oldest-first.

    Confirmed live 2026-07-12: with a narrowed ±1h window (11:00-13:00) but
    a budget that only fits ~6 of 20 candidates, plain chronological
    (oldest-first) ordering packed the window's EARLIEST entries (11:07)
    and never reached the entries actually near noon (12:30) — the
    requested moment, not "earliest in window", is what proximity should be
    measured against.
    """
    center = 300.0  # e.g. "noon" in this toy timeline
    results = [
        _r("far before", mechanisms=["temporal"], ts=100.0),   # |100-300|=200
        _r("closest", mechanisms=["temporal"], ts=290.0),      # |290-300|=10
        _r("far after", mechanisms=["temporal"], ts=500.0),    # |500-300|=200
        _r("second closest", mechanisms=["temporal"], ts=320.0),  # |320-300|=20
    ]
    block, count = fit_search_results(results, 1000, temporal_center=center)
    lines = block.split("\n")
    assert lines[0].endswith("closest")
    assert lines[1].endswith("second closest")


def test_temporal_center_none_falls_back_to_chronological():
    """Without a known center (e.g. a day-only window with no specific time
    requested), fall back to the existing oldest-first behaviour."""
    results = [
        _r("temporal newer", mechanisms=["temporal"], ts=200.0),
        _r("temporal older", mechanisms=["temporal"], ts=100.0),
    ]
    block, count = fit_search_results(results, 1000, temporal_center=None)
    lines = block.split("\n")
    assert lines[0].endswith("temporal older")
    assert lines[1].endswith("temporal newer")


def test_no_temporal_tags_behavior_unchanged():
    """Without any 'temporal' mechanism, packing order is exactly list order
    (regression guard for the base case)."""
    results = [_r("first"), _r("second"), _r("third")]
    block, count = fit_search_results(results, 1000)
    assert block.split("\n") == ["- first", "- second", "- third"]
