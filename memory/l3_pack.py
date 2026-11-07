"""memory.l3_pack — pack L3 results into the prompt budget, originals first, wave-2 only in what is left.

Success criterion for wave 2 (Laris, 2026-11-07): relevant neighbours WITHOUT degrading the original
retrieval. So the originals are packed exactly as they would be with wave 2 off (same order, same
budget cut), and neighbours only take the budget that is left, only for seeds that made it in. Under
pressure neighbours are dropped, never originals; `info` records both sets so l3_usage can prove it.
"""
from __future__ import annotations

from datetime import datetime

from memory.budget import estimate_tokens
from memory.l3_attribution import pack_order

__all__ = ["render_line", "pack_lines", "fit_originals_first"]


def render_line(r: dict) -> str:
    ts = r["metadata"].get("timestamp", 0)
    dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
    return f"- [{dt}] {r['content']}" if dt else f"- {r['content']}"


def pack_lines(results: list[dict], max_tokens: int, temporal_center: float | None = None):
    """Greedy pack in pack_order until the first line that does not fit; returns (lines, packed, tokens)."""
    lines, packed, total = [], [], 0
    for r in pack_order(results, temporal_center):
        line = render_line(r)
        tok = estimate_tokens(line)
        if total + tok > max_tokens and lines:
            break
        lines.append(line)
        packed.append(r)
        total += tok
    return lines, packed, total


def _is_wave2(r: dict) -> bool:
    return "wave2" in r.get("mechanisms", [])


def _key(r: dict) -> str:
    return str((r.get("metadata") or {}).get("message_id") or r.get("content", "")[:40])


def fit_originals_first(results: list[dict], max_tokens: int, temporal_center: float | None = None):
    """(block, count, packed, info): originals packed as without wave 2, neighbours in the leftover only."""
    originals = [r for r in results if not _is_wave2(r)]
    _, base, used = pack_lines(originals, max_tokens, temporal_center)
    seeds_in = {(r.get("metadata") or {}).get("message_id") for r in base}
    chosen = {id(r) for r in base}
    left = max_tokens - used
    for n in (r for r in results if _is_wave2(r)):
        tok = estimate_tokens(render_line(n))
        if n.get("wave2_of") in seeds_in and tok <= left:
            chosen.add(id(n))
            left -= tok
    packed = [r for r in pack_order(results, temporal_center) if id(r) in chosen]
    before = [_key(r) for r in base]
    after = [_key(r) for r in packed if not _is_wave2(r)]
    info = {"l3_budget": max_tokens, "orig_before": before, "orig_after": after,
            "orig_replaced": sorted(set(before) - set(after))}
    return "\n".join(render_line(r) for r in packed), len(packed), packed, info
