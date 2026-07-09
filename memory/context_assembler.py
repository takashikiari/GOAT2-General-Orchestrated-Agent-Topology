"""memory.context_assembler — pure L0-L3 prompt block assembly.

No I/O, no state. All functions are deterministic given their inputs.
Called by MemoryLayers.assemble_context after the tiers have been fetched.
"""
from __future__ import annotations

from datetime import datetime

from memory.budget import estimate_tokens
from memory.config_extra import BLENDED_MIN_SCORE as _BLENDED_MIN_SCORE
from memory.context_budget import allocate_context_budget


def assemble_blocks(
    budget: int,
    l3_results: list[dict] | None,
    facts: dict[str, str],
    identity_prompt: str,
    messages: list[dict],
    significance: float = 3.0,
) -> tuple[list[str], int]:
    """Build L0-L3 context blocks under ``budget`` tokens; returns (blocks, l3_used).

    L0+L1 mandatory. Budget split by allocate_context_budget: L2 gets its
    capped share, L3 gets the guaranteed minimum first (priority-inverted) so
    L2 cannot starve L3. Pre-scored results (blended_score present) are sorted
    and filtered directly; raw Chroma results go through the gap filter.
    """
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    identity = f"[Identity]\n{identity_prompt}\nCurrent time: {now}"
    if facts:
        identity += f"\n\nKnown facts:\n{format_facts(facts)}"
    mandatory_tokens = estimate_tokens(identity)
    blocks: list[str] = [identity]

    l2_cap, _ = allocate_context_budget(mandatory_tokens, budget)
    trimmed = trim_recent_messages(messages, l2_cap)
    l2_tokens = 0
    if trimmed:
        l2_block = f"[Conversation History]\n{format_messages(trimmed)}"
        l2_tokens = estimate_tokens(l2_block)
        blocks.append(l2_block)

    l3_used = 0
    if l3_results:
        l3_budget = max(budget - mandatory_tokens - l2_tokens, 0)
        if l3_budget > 0:
            if any("blended_score" in r for r in l3_results):
                ordered = sorted(
                    l3_results, key=lambda r: r.get("blended_score", 0.0), reverse=True,
                )
                relevant = blended_gap_filter(ordered, significance)
            else:
                relevant = gap_filter(l3_results, significance)
            l3_block, l3_used = fit_search_results(relevant, l3_budget)
            if l3_block:
                blocks.append(f"[Context recuperat din istoric]\n{l3_block}")
    return blocks, l3_used


def trim_recent_messages(messages: list[dict], max_tokens: int) -> list[dict]:
    """Keep the first (topic-setter) + most recent messages within max_tokens.

    The first message is pinned when it is small (< 25% of cap) so the opening
    context survives a pure recency trim. Returns oldest-first.
    """
    if not messages:
        return []

    def _tok(m: dict) -> int:
        return estimate_tokens(f"{m['role']}: {m['content']}")

    n = len(messages)
    pin_first = n > 1 and _tok(messages[0]) * 4 < max_tokens
    kept_idx: list[int] = []
    total = 0
    if pin_first:
        kept_idx.append(0)
        total += _tok(messages[0])
    for i in range(n - 1, -1, -1):
        if i == 0 and pin_first:
            continue
        tok = _tok(messages[i])
        if total + tok > max_tokens and kept_idx:
            break
        kept_idx.append(i)
        total += tok
    kept_idx.sort()
    return [messages[i] for i in kept_idx]


def fit_search_results(results: list[dict], max_tokens: int) -> tuple[str, int]:
    """Format results closest-first, keeping as many as fit max_tokens.

    Returns (block_text, count). Each line prefixed with [YYYY-MM-DD HH:MM].
    """
    lines: list[str] = []
    total = 0
    for r in results:
        ts = r["metadata"].get("timestamp", 0)
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
        line = f"- [{dt}] {r['content']}" if dt else f"- {r['content']}"
        tok = estimate_tokens(line)
        if total + tok > max_tokens and lines:
            break
        lines.append(line)
        total += tok
    return "\n".join(lines), len(lines)


def gap_filter(results: list[dict], significance: float = 3.0) -> list[dict]:
    """Keep results before the largest structural gap in the score distribution.

    Requires ≥3 results to compute a meaningful ratio; with fewer, a generous
    absolute ceiling (1.5 sq-L2, from V3 calibration) is applied instead.
    significance = max_gap / mean_gap; calibrated at 3.0 (V3, 2026-06-29).
    """
    if not results:
        return []
    if len(results) < 3:
        return [r for r in results if r.get("score", 0.0) < 1.5]
    scores = [r["score"] for r in results]
    gaps = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]
    max_gap = max(gaps)
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap == 0 or max_gap < significance * mean_gap:
        return []
    return results[: gaps.index(max_gap) + 1]


def blended_gap_filter(results: list[dict], significance: float = 3.0) -> list[dict]:
    """Gap filter for pre-scored (blended_score descending) results.

    Falls back to _BLENDED_MIN_SCORE cutoff when the distribution is uniform.
    """
    if not results:
        return []
    if len(results) < 3:
        return [r for r in results if r.get("blended_score", 0.0) >= _BLENDED_MIN_SCORE]
    scores = [r.get("blended_score", 0.0) for r in results]
    gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
    max_gap = max(gaps)
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap > 0 and max_gap >= significance * mean_gap:
        return results[: gaps.index(max_gap) + 1]
    return [r for r in results if r.get("blended_score", 0.0) >= _BLENDED_MIN_SCORE]


def format_facts(facts: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in facts.items())


def format_messages(messages: list[dict]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)
