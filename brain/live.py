"""brain.live — the living memory graph inside a turn (design §4, §5, §12; Laris, 2026-11-17).

Before the prompt (``recall``): what is in mind right now — the memories retrieval brought and the episodes of the
current conversation — lights up the graph; the most activated memories that are not already in mind join the L3
results, each with why it came up ("amintit prin legătură: …"). Association brings them, not text similarity. They
come AFTER the search's 3 best results (a score just under the 3rd), never in place of them: live 2026-11-17 17:28,
placed first they took every L3 slot and pushed out results of relevance 1.0, while the reply used almost none of
them. They compete for the space left.

After the reply (``after_turn``, in the background, never on the reply path): the new turn is linked to the memories
that were in its prompt — strongly to the ones the reply used ('gândite_împreună' +0.2, +0.05 if only present), the
used ones are linked to each other (+0.1), and every link walked to a recalled memory is touched (+0.2 if that memory
was used, +0.05 if not). Links are formed in the same turn, from what was active then.

Off unless [brain] enabled and graph_recall. Any error is logged and the turn goes on as without the graph.
Nothing is ever deleted (brain/graph.py).
"""
from __future__ import annotations

import asyncio
import time

from brain import brain as _brain
from brain import graph, recall
from memory.usage_probe import MIN_OVERLAP, MIN_RATIO, _terms
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["recall_into", "after_turn", "used"]


def _on() -> bool:
    return _brain.is_enabled() and bool(_brain.CONFIG.get("graph_recall"))


_NOT_LIVED = ("Mesaje noi pe canalul agent-coord", "[Workflow '")


def _not_lived(doc: str | None) -> bool:
    """An agent-coord digest or a workflow report: the traffic of other sessions, never recalled into a turn.
    Never when [mind] is on: agent-coord messages (lane ``channel``) are lived memory like any conversation."""
    if _brain.mind_on():
        return False
    text = (doc or "").lstrip()
    if text.startswith("[") and "] " in text[:40] and text[1:5].isdigit():  # "[2026-11-14T17:38:23+02:00] "
        text = text.split("] ", 1)[1].lstrip()
    if text.startswith("user:"):
        text = text[5:].lstrip()
    return text.startswith(_NOT_LIVED)


def _after_top(l3_results: list[dict], top: int = 3) -> float:
    """The score the recalled memories get: just under the search's ``top``-th best result, so they never push out
    the best ones (when the search found fewer, just under its weakest; 0.5 when it found nothing)."""
    scores = sorted((float(r.get("blended_score") or 0.0) for r in l3_results
                     if "wave2" not in (r.get("mechanisms") or [])), reverse=True)
    if not scores:
        return 0.5
    return max(0.0, scores[min(top, len(scores)) - 1] * 0.99)


def _mid(r: dict) -> str:
    return str((r.get("metadata") or {}).get("message_id") or r.get("id") or "")


def used(text: str, reply_terms: set[str]) -> bool:
    """usage_probe's rule: the reply shares >= 3 and >= 10% of the entry's distinctive terms."""
    terms = _terms(text or "")
    common = len(terms & reply_terms)
    return bool(terms) and common >= MIN_OVERLAP and common / len(terms) >= MIN_RATIO


async def recall_into(episodic, l3_results: list[dict], messages: list[dict]) -> tuple[list[dict], dict]:
    """``l3_results`` with the graph's recalled memories in front, and the trace {memory id: path} for after_turn.
    Unchanged (and an empty trace) when off or on any error."""
    if not _on():
        return l3_results, {}
    t0 = time.perf_counter()
    try:
        seeds = {_mid(r): max(float(r.get("blended_score") or 0.0), 0.05) for r in l3_results
                 if "wave2" not in (r.get("mechanisms") or []) and _mid(r)}
        for m in reversed(messages[-20:]):
            if m.get("l3_id"):
                seeds.setdefault(str(m["l3_id"]), 0.6)
        if not seeds:
            return l3_results, {}
        brain, now = _brain.get_instance(), time.time()
        k = int(_brain.CONFIG.get("graph_recall_k", 3))
        half = float(_brain.CONFIG.get("graph_half_life_days", 14.0))
        async with brain.lock:
            cands = await asyncio.to_thread(recall.spread, brain._db, seeds, now, k * 4, 3, half)
        if not cands:
            return l3_results, {}
        got = await asyncio.to_thread(episodic._get_collection().get, ids=[c["id"] for c in cands],
                                      include=["documents", "metadatas"])
        by_id = {i: (d, m) for i, d, m in zip(got.get("ids") or [], got.get("documents") or [],
                                               got.get("metadatas") or [])}
        extra, trace = [], {}
        after = _after_top(l3_results)
        for c in cands:
            if c["id"] not in by_id or len(extra) >= k:
                continue
            doc, meta = by_id[c["id"]]
            if _not_lived(doc):  # other sessions' traffic, not GOAT's experience with Laris
                continue
            why = f"(amintit prin legătură{': ' + c['via'] if c['via'] else ''}) "
            extra.append({"id": c["id"], "content": why + (doc or ""), "blended_score": after, "mechanisms": ["graph"],
                          "metadata": dict(meta or {}, message_id=(meta or {}).get("message_id") or c["id"])})
            trace[c["id"]] = c["path"]
    except Exception as exc:  # noqa: BLE001
        log.warning("brain: graph recall failed: %s: %s", type(exc).__name__, exc)
        return l3_results, {}
    log.info("brain: graph recall seeds=%d recalled=%d ms=%.1f", len(seeds), len(extra),
             (time.perf_counter() - t0) * 1000)
    return extra + l3_results, trace


async def after_turn(turn_id: str, l3_results: list[dict], provenance: dict, reply: str, trace: dict) -> None:
    """Form this turn's links and touch the walked ones (see the module docstring). Background; errors swallowed."""
    if not (_on() and turn_id):
        return
    try:
        packed = [m for m in (provenance or {}).get("orig_after", []) if m]
        text = {_mid(r): str(r.get("content") or "") for r in l3_results}
        rterms = _terms(reply or "")
        was_used = {m: used(text.get(m, ""), rterms) for m in packed}
        brain, now = _brain.get_instance(), time.time()

        def _write() -> tuple[int, int]:
            links = touched = 0
            for m, u in was_used.items():
                graph.touch(brain._db, turn_id, m, "gândite_împreună", 0.2 if u else 0.05, now)
                links += 1
            ok = [m for m, u in was_used.items() if u]
            for i in range(len(ok)):
                for j in range(i + 1, len(ok)):
                    graph.touch(brain._db, ok[i], ok[j], "gândite_împreună", 0.1, now)
                    links += 1
            for m, path in (trace or {}).items():
                if m not in was_used:
                    continue
                for src, dst, kind in path:
                    graph.touch(brain._db, src, dst, kind, 0.2 if was_used[m] else 0.05, now)
                    touched += 1
            brain._db.commit()
            return links, touched
        async with brain.lock:
            links, touched = await asyncio.to_thread(_write)
        log.info("brain: graph turn links=%d touched=%d used=%d recalled_in_prompt=%d", links, touched,
                 sum(was_used.values()), sum(1 for m in trace or {} if m in was_used))
    except Exception as exc:  # noqa: BLE001
        log.warning("brain: graph after_turn failed: %s: %s", type(exc).__name__, exc)
