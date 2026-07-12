"""tests.test_fit_search_results — unit tests for fit_search_results.

Pure function: no I/O, no mocks. Covers the base closest-first packing
behaviour plus the temporal-tagged protected-budget fix (bug 1: a result
rescued by blended_gap_filter's temporal carve-out, then re-sorted to the
back of the list by blended_score, must not be silently dropped again by
fit_search_results just because it now sits near the end of `results`).
"""
from __future__ import annotations

from memory.context_assembler import fit_search_results


def _r(content: str, mechanisms: tuple = ()) -> dict:
    return {
        "content": content,
        "metadata": {"timestamp": 0.0},
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


def test_temporal_results_packed_in_their_own_best_first_order_first():
    """Multiple temporal results: packed before any non-temporal result,
    in their own relative (best-first) order; remaining budget then goes
    to the rest in their relative order."""
    results = [
        _r("non-temporal high"),
        _r("temporal best", mechanisms=["temporal"]),
        _r("temporal second", mechanisms=["temporal"]),
    ]
    block, count = fit_search_results(results, 1000)
    lines = block.split("\n")
    assert lines[0].endswith("temporal best")
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


def test_no_temporal_tags_behavior_unchanged():
    """Without any 'temporal' mechanism, packing order is exactly list order
    (regression guard for the base case)."""
    results = [_r("first"), _r("second"), _r("third")]
    block, count = fit_search_results(results, 1000)
    assert block.split("\n") == ["- first", "- second", "- third"]
