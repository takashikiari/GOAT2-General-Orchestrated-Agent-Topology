"""brain.boost — the prefetch feedback, phase 1: L3 docs that were really used rank a little higher (2026-11-15).

After the CrossEncoder rerank (memory/retrieval.py, drift and cold), each original result whose doc was injected
into earlier prompts (brain/usage.py, faded count ``uses``) gets

    score_boost = weight * min(1, uses / cap)        weight <= 0.05 (hard), cap >= 1
    blended_score = min(1.0, ce_score + score_boost)

``ce_score`` keeps the CrossEncoder's score and ``score_boost`` the bonus, so nothing is lost and both are visible.
The boosted ``blended_score`` is what context_assembler's sort, gap filter and packing see (the same discipline as
memory/entity_boost.py: additive, bounded, re-sorted). Wave-2 neighbours are never boosted and stay after the seeds,
in their order. Off by default ([brain] prefetch_boost); any error returns the results untouched.
"""
from __future__ import annotations

import time

from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["MAX_WEIGHT", "apply", "rerank_boost"]

MAX_WEIGHT = 0.05  # hard bound: the config can lower it, never raise it


def _mid(r: dict) -> str | None:
    mid = (r.get("metadata") or {}).get("message_id")
    return str(mid) if mid else None


def _is_wave2(r: dict) -> bool:
    return "wave2" in (r.get("mechanisms") or [])


def apply(results: list[dict], uses: dict[str, float], weight: float, cap: float) -> tuple[list[dict], dict]:
    """The results re-ordered with the bounded boost, and what it did: boosted (how many got a bonus), moved (seeds
    whose position changed), max_delta (largest bonus), top3_changed (seeds that entered or left the top 3, which is
    about what reaches the prompt: prefetch_quality used=3 on the live turns). Pure: the input is not modified."""
    weight, cap = min(max(float(weight), 0.0), MAX_WEIGHT), max(float(cap), 1.0)
    seeds = [r for r in results if not _is_wave2(r)]
    neighbours = [r for r in results if _is_wave2(r)]
    boosted, out = 0, []
    for r in seeds:
        bonus = weight * min(1.0, float(uses.get(_mid(r) or "", 0.0)) / cap)
        if bonus > 0:
            ce = float(r.get("blended_score", 0.0))
            r = {**r, "ce_score": ce, "score_boost": bonus, "blended_score": min(1.0, ce + bonus)}
            boosted += 1
        out.append(r)
    order = sorted(range(len(out)), key=lambda i: out[i].get("blended_score", 0.0), reverse=True)  # stable on ties
    ranked = [out[i] for i in order]
    before_top3 = {id(seeds[i]) for i in range(min(3, len(seeds)))}
    after_top3 = {id(seeds[i]) for i in order[:3]}
    stats = {"boosted": boosted, "moved": sum(1 for pos, i in enumerate(order) if pos != i),
             "max_delta": round(max((r.get("score_boost", 0.0) for r in out), default=0.0), 4),
             "top3_changed": len(after_top3 - before_top3)}
    return ranked + neighbours, stats


async def rerank_boost(chat_id: str, results: list[dict]) -> list[dict]:
    """The boost on live results, or ``results`` unchanged when [brain] enabled / prefetch_boost is off, nothing was
    ever used, or anything fails (a broken brain never costs the retrieval). Logs one line per boosted turn."""
    from brain import brain as _brain
    if not results or not (_brain.is_enabled() and _brain.CONFIG.get("prefetch_boost")):
        return results
    t0 = time.perf_counter()
    try:
        from brain import usage
        uses = await usage.scores(_brain.get_instance(), [m for m in map(_mid, results) if m])
        if not uses:
            return results
        ranked, stats = apply(results, uses, _brain.CONFIG.get("prefetch_boost_weight", MAX_WEIGHT),
                              _brain.CONFIG.get("prefetch_boost_cap", 3))
    except Exception as exc:  # noqa: BLE001
        log.warning("brain: prefetch_boost failed chat=%s: %s: %s", chat_id, type(exc).__name__, exc)
        return results
    log.info("brain: prefetch_boost chat=%s boosted=%d moved=%d max_delta=%.4f top3_changed=%d ms=%.1f", chat_id,
             stats["boosted"], stats["moved"], stats["max_delta"], stats["top3_changed"],
             (time.perf_counter() - t0) * 1000)
    return ranked
