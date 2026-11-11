"""orchestrator.tool_gate — how good this turn's L3 prefetch is, and whether search_memory is offered at all.

Memory tools are a fallback; the prefetch does the work (Laris via GOAT, 2026-11-11). On 45 real turns (replayed
read-only on copies of the stores) the model still called search_memory once with an excellent prefetch (top rerank
score 0.981, 8 entries injected) — a wasted call — and once with a weak one (0.012). So the quality of the prefetch,
not the number of entries (every turn injects something), decides:

- ``measure`` takes what the turn already has: the reranked L3 results (``blended_score`` is sigmoid(cross-encoder
  logit) once the reranker ran), how many entries were used in the prompt and their tokens. Wave-2 neighbours are
  left out of the scores (theirs are copied from their seed).
- ``decide``: search_memory would be hidden when the best score is at least ``search_memory_min_top_score`` (0.5 is the
  cross-encoder's own boundary, logit 0) AND at least one entry was used. It stays whenever there is nothing to judge
  by: no results, nothing used, or no reranker (scores are then not comparable) — the fallback is never cut exactly
  when the prefetch may be degraded.
- ``log_line``: one INFO line per turn (``prefetch_quality …``) with the numbers, the decision and which branch made it,
  to read the distribution of good vs weak prefetches from the log.

Settings: ``[tool_gate]`` in config/memory.toml. ``enabled = false`` by default: the line is written and the decision is
computed (``would_hide``) but the tool is never hidden until the threshold is confirmed on real turns.
"""
from __future__ import annotations

from dataclasses import dataclass

from memory.config import _cfg
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["GateConfig", "Quality", "load", "measure", "decide", "log_line", "CONFIG", "GATED_TOOL"]

GATED_TOOL = "search_memory"


@dataclass(frozen=True)
class GateConfig:
    enabled: bool = False
    min_top_score: float = 0.5


@dataclass(frozen=True)
class Quality:
    top: float | None  # best rerank score among the original (non wave-2) results; None: no results
    top3: float  # sum of the best three
    found: int  # results the prefetch returned
    used: int  # entries that made it into the prompt
    tokens: int  # their tokens
    reranked: bool  # the cross-encoder ran, so the scores mean something


def load(cfg: dict) -> GateConfig:
    section = cfg.get("tool_gate", {}) or {}
    return GateConfig(enabled=bool(section.get("enabled", False)),
                      min_top_score=float(section.get("search_memory_min_top_score", 0.5)))


CONFIG = load(_cfg)


def measure(results: list[dict], used: int, tokens: int, reranked: bool) -> Quality:
    scores = sorted((float(r.get("blended_score") or 0.0) for r in results
                     if "wave2" not in (r.get("mechanisms") or [])), reverse=True)
    return Quality(top=scores[0] if scores else None, top3=sum(scores[:3]), found=len(results), used=int(used or 0),
                   tokens=int(tokens or 0), reranked=bool(reranked))


def decide(q: Quality, cfg: GateConfig) -> dict:
    """``would_hide``: the prefetch is good enough for search_memory to be left out; ``hidden``: it actually is."""
    if not q.reranked:
        why, good = "no_rerank", False
    elif q.top is None:
        why, good = "no_results", False
    elif q.used == 0:
        why, good = "l3_used=0", False
    elif q.top >= cfg.min_top_score:
        why, good = f"top>={cfg.min_top_score:g}", True
    else:
        why, good = f"top<{cfg.min_top_score:g}", False
    return {"would_hide": good, "hidden": good and cfg.enabled, "why": why}


def log_line(chat_id: str, q: Quality, d: dict, cfg: GateConfig) -> None:
    top = "none" if q.top is None else f"{q.top:.3f}"
    log.info("prefetch_quality chat=%s top=%s top3=%.3f found=%d used=%d l3_tokens=%d reranked=%d gate=%s "
             "min_top=%g would_hide=%d hidden=%d why=%s", chat_id, top, q.top3, q.found, q.used, q.tokens,
             int(q.reranked), "on" if cfg.enabled else "off", cfg.min_top_score, int(d["would_hide"]),
             int(d["hidden"]), d["why"])
