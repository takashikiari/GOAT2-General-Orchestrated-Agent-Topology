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


def test_cache_lookups_counted_only_when_a_key_was_looked_up():
    a = MemoryAnalytics()
    warm = _obs(["working"])                       # warm/drift turn: no cache lookup, no key
    cold_miss = _obs(["episodic"]); cold_miss.cache_key = "search:aaa"
    cold_hit = _obs(["episodic"]); cold_hit.cache_key = "search:bbb"; cold_hit.cache_hit = True; cold_hit.cache_miss = False
    for o in (warm, warm, cold_miss, cold_hit):
        a.record(o)
    r = a.get_report()
    assert r["cache_lookups"] == 2 and r["cache_lookup_rate"] == 0.5
    assert r["cache_hit_rate"] == 0.25 and r["cache_hit_rate_on_lookups"] == 0.5


def test_no_lookups_reports_none_not_zero():
    a = MemoryAnalytics()
    a.record(_obs(["working"]))
    assert a.get_report()["cache_hit_rate_on_lookups"] is None


def test_token_breakdown_explains_the_api_prompt():
    a = MemoryAnalytics()
    o = _obs(["working"])
    o.tokens_l0_l1, o.tokens_l2, o.tokens_l3 = 1000, 2000, 1000
    o.tokens_guidance, o.tokens_tools = 300, 3500
    o.tokens_injected = 8000          # 4000 tiers + 300 guidance + 3500 tools + 200 framing
    o.llm_calls, o.tokens_prompt_api = 3, 27000   # 3 × 8000 resent context + 3000 tool results
    a.record(o)
    r = a.get_report()
    assert r["avg_tokens_other"] == 27000 - 4000
    assert r["avg_tokens_guidance"] == 300 and r["avg_tokens_tools"] == 3500
    assert r["avg_tokens_injected_unattributed"] == 200
    assert r["avg_tokens_resent_est"] == 16000 and r["avg_tokens_tool_results_est"] == 3000
    assert r["avg_prompt_tokens_per_llm_call"] == 9000
    for k in ("avg_tokens_injected", "avg_tokens_l0_l1", "avg_tokens_l2", "avg_tokens_l3", "avg_tokens_prompt_api"):
        assert k in r  # existing fields kept


def test_breakdown_never_negative_on_single_call():
    a = MemoryAnalytics()
    o = _obs(["working"]); o.tokens_injected, o.llm_calls, o.tokens_prompt_api = 9000, 1, 8000
    a.record(o)
    r = a.get_report()
    assert r["avg_tokens_resent_est"] == 0 and r["avg_tokens_tool_results_est"] == 0
