"""memory.context_assembler — pure L0-L3 prompt block assembly.

No I/O, no state. All functions are deterministic given their inputs.
Called by MemoryLayers.assemble_context after the tiers have been fetched.
"""
from __future__ import annotations

from datetime import datetime

from memory.budget import estimate_tokens
from memory.config_extra import BLENDED_MIN_SCORE as _BLENDED_MIN_SCORE, SESSION_GAP_SECONDS
from memory.context_budget import allocate_context_budget
from memory.date_format import format_duration, format_relative


def assemble_blocks(
    budget: int,
    l3_results: list[dict] | None,
    facts: dict[str, str],
    identity_prompt: str,
    messages: list[dict],
    significance: float = 3.0,
    temporal_center: float | None = None,
) -> tuple[list[str], int]:
    """Build L0-L3 context blocks under ``budget`` tokens; returns (blocks, l3_used).

    L0+L1 mandatory. Budget split by allocate_context_budget: L2 gets its
    capped share, L3 gets the guaranteed minimum first (priority-inverted) so
    L2 cannot starve L3. Pre-scored results (blended_score present) are sorted
    and filtered directly; raw Chroma results go through the gap filter.
    ``temporal_center`` (midpoint of a parsed date/time window, when the
    caller has one) is forwarded to fit_search_results — see its docstring.
    """
    now_dt = datetime.now().astimezone()
    now = now_dt.strftime("%Y-%m-%d %H:%M %Z")
    identity = f"[Identity]\n{identity_prompt}\nCurrent time: {now}"
    last_ts = messages[-1].get("timestamp") if messages else None
    if last_ts:
        identity += f"\nLast message: {format_relative(last_ts, now_dt.timestamp())}"
    if facts:
        identity += f"\n\nKnown facts:\n{format_facts(facts)}"
    mandatory_tokens = estimate_tokens(identity)
    blocks: list[str] = [identity]

    l2_cap, _ = allocate_context_budget(mandatory_tokens, budget, temporal=temporal_center is not None)
    trimmed = trim_recent_messages(messages, l2_cap)
    l2_tokens = 0
    if trimmed:
        l2_block = f"[Conversation History]\n{format_messages(trimmed, now_dt.timestamp())}"
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
            l3_block, l3_used = fit_search_results(relevant, l3_budget, temporal_center)
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


def fit_search_results(
    results: list[dict], max_tokens: int, temporal_center: float | None = None,
) -> tuple[str, int]:
    """Format results closest-first, keeping as many as fit max_tokens.

    Temporal-tagged results (``mechanisms`` includes ``"temporal"`` — matched
    an explicit date/time window the user named, per blended_gap_filter's
    rescue) get a protected first pass of the budget: they are packed before
    any non-temporal result, then any remaining budget is filled by the rest
    in their relative order. This mirrors context_budget.allocate_context_
    budget's guaranteed-minimum-first pattern one level down, inside L3's own
    packing — without it, a rescued temporal result that blended_gap_filter
    necessarily re-sorts toward the end of the list (its score is what needed
    rescuing) would fall off a tight budget exactly like the drop it was
    rescued from. Protection is not unlimited: with more temporal results
    than fit, only as many as fit are kept.

    Within the temporal group, results are ordered by NOT blended_score:
    date-window membership is itself the relevance signal for these results
    (the user named the window explicitly), so ranking them by textual
    similarity to the query is actively counterproductive whenever same-day
    self-referential conversation (e.g. debugging "why can't you remember
    X") is itself maximally similar to a "what did we discuss on X" query
    and would otherwise dominate the ranking. Confirmed live (2026-07-12): a
    real exchange from within the correct window was crowded out by
    same-day meta-conversation that scored higher on pure cross-encoder
    relevance. Only the LLM has enough context to judge relevance among
    genuinely-in-window content — this function's job is to get as much of
    that window to it as fits, not to pre-filter it by a score that can't
    tell the difference between the content and conversation about the
    content.

    ``temporal_center`` (the midpoint of the parsed date/time window, when
    the caller has it — orchestrator.py derives it from parse_interval's
    (after, before) tuple) orders the temporal group by closeness to that
    exact moment. Without it, falls back to oldest-first. Proximity beats
    plain chronology: confirmed live (2026-07-12) that with a narrowed
    ±1h window but a budget fitting only ~6 of 20 candidates, oldest-first
    packed the window's earliest entries and never reached content actually
    near the requested moment — "closest to what was asked", not "earliest
    in the window", is the right tiebreak once the window itself is already
    the coarse relevance filter.

    Returns (block_text, count). Each line prefixed with [YYYY-MM-DD HH:MM].
    """
    temporal = [r for r in results if "temporal" in r.get("mechanisms", [])]
    if temporal_center is not None:
        temporal.sort(key=lambda r: abs(r["metadata"].get("timestamp", 0) - temporal_center))
    else:
        temporal.sort(key=lambda r: r["metadata"].get("timestamp", 0))
    temporal_ids = {id(r) for r in temporal}
    rest = [r for r in results if id(r) not in temporal_ids]

    lines: list[str] = []
    total = 0
    for r in temporal + rest:
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

    Results tagged with the ``temporal`` mechanism (merge_results provenance —
    they matched an explicit date/time window the user named in the query)
    are never dropped by the cut, merged back in score order. The requested
    window is itself the relevance signal for those results; blended_score
    alone can't see it, and a same-day/self-referential result dominating the
    distribution otherwise erases them (confirmed on real production data,
    2026-07-12: a query naming a past date got its target memory ranked below
    same-session noise and gap-filtered down to one unrelated result).
    """
    if not results:
        return []
    if len(results) < 3:
        kept = [r for r in results if r.get("blended_score", 0.0) >= _BLENDED_MIN_SCORE]
    else:
        scores = [r.get("blended_score", 0.0) for r in results]
        gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
        max_gap = max(gaps)
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap > 0 and max_gap >= significance * mean_gap:
            kept = results[: gaps.index(max_gap) + 1]
        else:
            kept = [r for r in results if r.get("blended_score", 0.0) >= _BLENDED_MIN_SCORE]
    kept_ids = {id(r) for r in kept}
    rescued = [r for r in results if "temporal" in r.get("mechanisms", []) and id(r) not in kept_ids]
    if rescued:
        kept = sorted(kept + rescued, key=lambda r: r.get("blended_score", 0.0), reverse=True)
    return kept


def format_facts(facts: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in facts.items())


def format_messages(messages: list[dict], now: float | None = None) -> str:
    """Render L2 messages oldest-first, each prefixed with a relative timestamp.

    Inserts a "--- gap: X ---" separator when the gap between two consecutive
    messages meets SESSION_GAP_SECONDS, so a session boundary is visible in
    the transcript itself, not just as the single Identity-block line
    assemble_blocks adds separately. Messages missing a (truthy) timestamp
    render exactly as before — "role: content", no prefix.
    """
    now = now if now is not None else datetime.now().timestamp()
    lines: list[str] = []
    prev_ts: float | None = None
    for m in messages:
        ts = m.get("timestamp")
        if ts and prev_ts is not None and ts - prev_ts >= SESSION_GAP_SECONDS:
            lines.append(f"--- gap: {format_duration(ts - prev_ts)} ---")
        prefix = f"[{format_relative(ts, now)}] " if ts else ""
        lines.append(f"{prefix}{m['role']}: {m['content']}")
        if ts:
            prev_ts = ts
    return "\n".join(lines)
