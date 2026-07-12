"""memory.context_budget — priority-inverted AITS split across L2 and L3.

L3 gets first claim on a guaranteed minimum token slice (``L3_MIN_GUARANTEE_TOKENS``)
so it is never starved to zero by construction on realistic budgets; L2 takes the
remainder and so stays AITS-scaled (bigger budget -> more L2 room). This replaces
the old ``min(L2_CONTEXT_CAP, remaining - l3_reserve)`` split where L2 ate the
budget and L3 only ever got a 30% fraction (and 0 when ``has_l3`` was false). The
``L2_FLOOR_TOKENS`` guard keeps L2 alive on pathological sub-floor budgets; the
guarantee then shrinks to the remainder (``>= 0``). That guard never binds on
realistic AITS budgets (>= ~1700 after mandatory).
"""
from __future__ import annotations

from memory.config import L2_FLOOR_TOKENS, L3_MIN_GUARANTEE_TOKENS, TEMPORAL_L3_GUARANTEE_TOKENS

__all__ = ["allocate_context_budget"]


def allocate_context_budget(
    mandatory_tokens: int, budget: int, temporal: bool = False,
) -> tuple[int, int]:
    """Return ``(l2_cap, l3_guarantee)`` for the budget remaining after L0+L1.

    L3 has first claim on ``L3_MIN_GUARANTEE_TOKENS`` (or the wider
    ``TEMPORAL_L3_GUARANTEE_TOKENS`` when ``temporal=True`` — the current
    turn's query named an explicit date/time, per orchestrator.py's
    synchronous temporal fast-path; that user-named window is already the
    strongest possible relevance filter, so more of it should reach the LLM
    directly instead of being trimmed down by ranking heuristics). L2's cap
    is the remainder (``budget - mandatory - l3_guarantee``), so L2 scales
    with AITS while L3 is guaranteed. ``l3_guarantee`` is reserved regardless
    of whether L3 results exist (search is unconditional). On a budget too
    small to honour both L2's floor and the L3 guarantee, the L2 floor wins
    and the guarantee shrinks to the remainder (``>= 0``); this guard never
    binds on realistic budgets.

    Args:
        mandatory_tokens: estimated tokens already spent on mandatory L0+L1.
        budget: AITS per-intent token budget.
        temporal: use the wider temporal guarantee instead of the default.

    Returns:
        l2_cap: max tokens L2 may occupy (``>= 0``; ``<= available - guarantee``
            on realistic budgets, reduced to ``L2_FLOOR_TOKENS`` only sub-floor).
        l3_guarantee: tokens reserved for L3 (the applicable guarantee on
            realistic budgets; the remainder on sub-floor budgets).
    """
    available = max(budget - mandatory_tokens, 0)
    l3_guarantee = TEMPORAL_L3_GUARANTEE_TOKENS if temporal else L3_MIN_GUARANTEE_TOKENS
    l2_cap = max(available - l3_guarantee, 0)
    if l2_cap < L2_FLOOR_TOKENS:                       # L2 floor wins on tiny budgets
        l2_cap = min(L2_FLOOR_TOKENS, available)
        l3_guarantee = max(available - l2_cap, 0)
    return l2_cap, l3_guarantee