"""tests.test_context_budget — AITS budget split + smart L2 trim.

Covers Fix #1/#2 (L2 capped by its AITS share, L3 reserved so it isn't starved)
and Fix #5 (the topic-setter first message is pinned within the L2 cap).
"""
from __future__ import annotations

import asyncio

from memory.budget import estimate_tokens
from memory.context_budget import allocate_context_budget
from memory.context_assembler import trim_recent_messages
from memory.layers import MemoryLayers

# Representative config (config/memory.toml): L2_CONTEXT_CAP=8000,
# L3_RESERVE_FRACTION=0.3, L2_FLOOR_TOKENS=500, AITS hard cap=12000.

# --- allocate_context_budget: priority-inverted (L3 guaranteed first) -----------

def test_l3_guarantee_reserved_and_l2_takes_remainder():
    """Realistic budget: L3 gets its 1200 guarantee; L2 gets the rest (AITS-scaled)."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=4035)
    # available = 4025; l3_guarantee = 1200; l2_cap = 4025 - 1200 = 2825.
    assert l3_guarantee == 1200
    assert l2_cap == 2825
    assert l2_cap < 4035                       # L2 does not eat the whole budget


def test_l3_guarantee_never_zero_on_realistic_budget():
    """Even with no L3 results (search unconditional), the guarantee is reserved."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=4035)
    assert l3_guarantee > 0


def test_max_budget_l2_scales_with_aits_no_context_cap():
    """No L2_CONTEXT_CAP anymore: L2 grows with budget; L3 still only the guarantee floor."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=12000)
    assert l3_guarantee == 1200
    assert l2_cap == 12000 - 10 - 1200          # 10790 — L2 scales, no 8000 cap


def test_min_realistic_budget_keeps_l2_floor():
    """Min realistic AITS (~2800): guarantee reserved, L2 stays above its floor."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=2800)
    assert l3_guarantee == 1200
    assert l2_cap == 1590                       # 2790 - 1200
    assert l2_cap >= 500                         # L2 floor respected


def test_pathological_tiny_budget_l2_floor_wins():
    """Sub-floor budget: L2 floor wins, guarantee shrinks to the remainder (>=0)."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=510)
    # available = 500; can't honour 500 floor + 1200 guarantee -> floor wins, guarantee -> 0.
    assert l2_cap == 500
    assert l3_guarantee == 0


def test_zero_budget_yields_zero():
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=0)
    assert l2_cap == 0 and l3_guarantee == 0


# --- temporal=True: a wider dedicated L3 guarantee for explicit date/time queries -

def test_temporal_guarantee_larger_than_default_on_realistic_budget():
    """When the user named an explicit date/time (orchestrator's synchronous
    fast-path fired), L3 gets a much bigger dedicated slice than the default
    1200 — confirmed live 2026-07-12: a 20-candidate, ~2h-wide temporal
    window needed more than the default guarantee could fit, so the real
    exchange the user asked about lost out to closer, less-relevant content
    even after the gap-collapse and proximity-ordering fixes."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=4035, temporal=True)
    default_l2_cap, default_l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=4035)
    assert l3_guarantee > default_l3_guarantee
    assert l2_cap < default_l2_cap                 # L2 gives up room, not a bigger overall budget


def test_temporal_false_default_matches_existing_behavior():
    """temporal=False (the default, unnamed-parameter call site) is byte-for-byte
    the pre-existing behaviour — regression guard for every non-temporal turn."""
    assert allocate_context_budget(mandatory_tokens=10, budget=4035) == \
        allocate_context_budget(mandatory_tokens=10, budget=4035, temporal=False)


def test_temporal_guarantee_still_respects_l2_floor():
    """Even with the wider temporal guarantee, L2 never drops below its floor —
    the current turn's own immediate conversation never fully vanishes."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=4035, temporal=True)
    assert l2_cap >= 500


# --- _trim_recent_messages: pin the topic-setter, keep newest ----------------

def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content, "timestamp": 0.0}


def test_trim_pins_first_message_plus_recent():
    """First (topic-setter) survives alongside the recent tail."""
    msgs = [_msg("user", f"open{i}") for i in range(20)]
    cap = 8 * 4  # 8 tokens ≈ 32 chars → only a few short messages fit
    kept = trim_recent_messages(msgs, cap)
    assert kept[0] is msgs[0]                    # opening message pinned
    assert kept[-1] is msgs[-1]                  # newest also kept


def test_trim_single_message_kept():
    kept = trim_recent_messages([_msg("user", "hello world")], 8000)
    assert len(kept) == 1


def test_trim_empty():
    assert trim_recent_messages([], 8000) == []


def test_trim_returns_oldest_first():
    msgs = [_msg("user", "a"), _msg("assistant", "b"), _msg("user", "c")]
    kept = trim_recent_messages(msgs, 8000)
    assert [m["content"] for m in kept] == ["a", "b", "c"]


# --- assemble_context: priority-inverted split + similarity filter ---------------

class _FakePermanent:
    async def get_all_facts(self):
        return {}


class _FakeWorking:
    def __init__(self, messages):
        self._m = list(messages)

    async def get_messages(self, chat_id):
        return list(self._m)

    async def save_messages(self, chat_id, messages):
        self._m = messages


def _layers(messages):
    return MemoryLayers(_FakeWorking(messages), episodic=None, permanent=_FakePermanent())


def _res(content, score):
    return {"content": content, "metadata": {"timestamp": 0.0}, "score": score}


def test_assemble_filters_l3_by_gap():
    """Gap filter keeps the cluster before the structural break; distant outlier excluded.

    scores=[0.30, 0.35, 0.40, 0.45, 2.00]: gaps=[0.05, 0.05, 0.05, 1.55],
    mean=0.425, ratio=3.65 > 3.0 → keeps first 4, drops the distant result.
    """
    layers = _layers([])
    results = [
        _res("close A", 0.30),
        _res("close B", 0.35),
        _res("close C", 0.40),
        _res("close D", 0.45),
        _res("distant E", 2.00),
    ]
    blocks, l3_used = asyncio.run(
        layers.assemble_context("c", budget=4000, l3_results=results)
    )
    related = [b for b in blocks if b.startswith("[Context recuperat din istoric]")]
    assert len(related) == 1
    assert "close A" in related[0]
    assert "close D" in related[0]
    assert "distant E" not in related[0]
    assert l3_used == 4


def test_assemble_uniform_l3_injects_nothing():
    """Uniform score distribution (no structural gap) → gap filter returns [] → no injection."""
    layers = _layers([])
    results = [_res(f"doc{i}", 1.0 + i * 0.1) for i in range(6)]  # 1.0, 1.1, ... 1.5
    blocks, l3_used = asyncio.run(
        layers.assemble_context("c", budget=4000, l3_results=results)
    )
    related = [b for b in blocks if b.startswith("[Context recuperat din istoric]")]
    assert len(related) == 0
    assert l3_used == 0


def test_assemble_l2_cap_reserves_l3_guarantee():
    """L2 fills its share but the L3 guarantee is held back (L2 < budget)."""
    big = [_msg("user", "x" * 400) for _ in range(50)]  # ~100 tok each
    layers = _layers(big)
    blocks, _ = asyncio.run(layers.assemble_context("c", budget=4000, l3_results=[]))
    history = [b for b in blocks if b.startswith("[Conversation History]")][0]
    tok = estimate_tokens(history)
    assert 2000 < tok < 3900                             # L2 got remainder MINUS the guarantee


def test_assemble_l3_guaranteed_when_l2_small():
    """Simple turn (tiny L2): L3 gets the full remainder, >= the guarantee."""
    layers = _layers([])                                # no L2 history
    results = [_res("only result", 0.3)]
    blocks, l3_used = asyncio.run(
        layers.assemble_context("c", budget=4000, l3_results=results)
    )
    assert l3_used == 1
    assert any(b.startswith("[Context recuperat din istoric]") for b in blocks)


# --- assemble_context: "Ultimul mesaj" identity line ---------------------------

def test_assemble_identity_includes_last_message_relative_time():
    """Identity block surfaces 'Last message: X ago' from the last L2 message's timestamp."""
    import time
    last_ts = time.time() - 1200  # 20 min ago
    msgs = [{"role": "user", "content": "salut", "timestamp": last_ts}]
    layers = _layers(msgs)
    blocks, _ = asyncio.run(layers.assemble_context("c", budget=4000, l3_results=[]))
    assert "Last message: 20 min ago" in blocks[0]


def test_assemble_identity_omits_last_message_line_when_no_history():
    layers = _layers([])
    blocks, _ = asyncio.run(layers.assemble_context("c", budget=4000, l3_results=[]))
    assert "Last message" not in blocks[0]


def test_assemble_identity_omits_last_message_line_for_falsy_timestamp():
    """The _msg() helper's timestamp: 0.0 (used throughout this file) must not produce a line."""
    layers = _layers([_msg("user", "salut")])
    blocks, _ = asyncio.run(layers.assemble_context("c", budget=4000, l3_results=[]))
    assert "Last message" not in blocks[0]


# --- assemble_context: temporal_center widens L3's room -------------------------

def test_assemble_widens_l3_budget_for_temporal_queries():
    """temporal_center threads through assemble_context -> the wider
    TEMPORAL_L3_GUARANTEE_TOKENS guarantee lets more of a real date-window's
    results survive packing than the default guarantee, even with plenty of
    L2 content competing for the same overall budget (confirmed live
    2026-07-12: the default guarantee was too tight for a real 20-candidate
    temporal window)."""
    # 100 messages (~5400 tok) comfortably exceeds L2's cap under EITHER
    # guarantee, so L2 fills to its cap both times and the L3 budget
    # difference cleanly reflects the guarantee gap (1200 vs 4000), not an
    # incidental L2-content shortfall.
    big_l2 = [_msg("user", "x" * 400) for _ in range(100)]
    layers = _layers(big_l2)

    def _temporal_res(i):
        return {
            "content": f"target entry number {i} " + ("word " * 300),  # ~310 tok each
            "metadata": {"timestamp": float(i)},
            "blended_score": 0.5,
            "mechanisms": ["temporal"],
        }

    results = [_temporal_res(i) for i in range(6)]  # ~1860 tok total: fits under the
    # temporal guarantee (4000) but not the default one (1200).

    _, l3_used_default = asyncio.run(
        layers.assemble_context("c", budget=6000, l3_results=results)
    )
    _, l3_used_temporal = asyncio.run(
        layers.assemble_context("c", budget=6000, l3_results=results, temporal_center=3.0)
    )
    assert l3_used_temporal > l3_used_default
    assert l3_used_temporal == 6      # all fit under the wider temporal guarantee
    assert l3_used_default < 6        # not all fit under the default one