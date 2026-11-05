"""tests.test_analytics — MemoryAnalytics.get_report()'s tier_hit_rates.

Regression test: tier_hit_rates used to source from tier_hits (one label per
turn — the single highest-priority tier per observability_collector's
episodic > working > permanent order), silently hiding that most turns
inject more than one tier at once (L0/L1 identity is mandatory every turn,
L2 history whenever present). It now sources from tier_presence, a
multi-label counter — every tier a turn actually used gets counted, so the
rates can (correctly) sum past 100%.
"""
from __future__ import annotations

from memory.analytics import MemoryAnalytics
from memory.observability import MemoryObservation


def _obs(tiers_used: list[str], source_tier: str = "") -> MemoryObservation:
    return MemoryObservation(
        timestamp=0.0,
        chat_id="chat1",
        user_message="hi",
        confidence=0.5,
        complexity=0.1,
        intent_category="conversational",
        budget_allocated=100,
        budget_used=50,
        cache_hit=False,
        cache_miss=True,
        source_tier=source_tier,
        tiers_used=tiers_used,
    )


def test_tier_hit_rates_counts_every_tier_present_per_turn():
    analytics = MemoryAnalytics()
    # Every turn here injects both permanent (identity) and working (history);
    # only the second one also has episodic (retrieved context).
    analytics.record(_obs(["permanent", "working"], source_tier="working"))
    analytics.record(_obs(["episodic", "permanent", "working"], source_tier="episodic"))

    report = analytics.get_report()

    # 2/2 turns had permanent and working; 1/2 had episodic — rates sum to 250%,
    # which is correct for a multi-label metric, not a bug.
    assert report["tier_hit_rates"]["permanent"] == 1.0
    assert report["tier_hit_rates"]["working"] == 1.0
    assert report["tier_hit_rates"]["episodic"] == 0.5


def test_tier_hit_rates_empty_when_no_turns_recorded():
    analytics = MemoryAnalytics()
    assert analytics.get_report()["tier_hit_rates"] == {}


def test_tier_hits_single_label_counter_still_populated_for_benchmark_compat():
    # benchmark/runner.py reads analytics.tier_hits directly (not through
    # get_report) — this must keep working exactly as before.
    analytics = MemoryAnalytics()
    analytics.record(_obs(["episodic", "permanent", "working"], source_tier="episodic"))
    assert analytics.tier_hits == {"episodic": 1}
