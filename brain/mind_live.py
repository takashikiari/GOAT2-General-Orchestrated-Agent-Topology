"""brain.mind_live — the working memory ("În minte") inside a turn (spec 2026-11-21 §2 F1, F4, F7).

``expose``, before the reply: what was in mind fades; search hits, the message's cues (spreading along the typed graph
links to memories the search never returned) and the open items light ONE working memory; its top becomes one
Romanian block — what is known, what happened (each with why), what is open, what is missing. Any error: logged, the
turn goes on without the block. Nothing is ever deleted (brain/mind.py).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from brain import brain as _brain
from brain import graph, mind, recall
from brain.understand import understand_message
from memory.budget import estimate_tokens
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["expose"]

_EPS = 1e-9  # "above floor": an item faded to the floor is no longer in mind
_RAW_N, _RAW_MAX, _GIST_MAX = 3, 600, 160
_POOL = 200  # amintire items read as spread seeds


def _mid(r: dict) -> str:
    return str((r.get("metadata") or {}).get("message_id") or r.get("id") or "")


def _day(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return "dată necunoscută"


def _things(db, about: list[str]) -> dict[str, str | None]:
    """{cue label: its thing node id, or None when the graph has no node for it}. Labels norm_thing rejects ("UK",
    generic words) are not cues at all: skipped, never reported as missing (R14)."""
    out: dict[str, str | None] = {}
    for label in about:
        key = graph.norm_thing(label, "project")
        if key:
            tid = f"thing:{key}"
            out[label] = tid if db.execute("SELECT 1 FROM graph_nodes WHERE id = ?", (tid,)).fetchone() else None
    return out


def _associate(db, chat_id: str, lane: str, things: dict, cfg: dict, half: float, now: float) -> None:
    """Spread from the cue things, then from the memories in mind (R2); light every memory reached. Two walks, so a
    search hit that a cue also reaches is lit from both sides (a seed is never part of its own walk's result). Each
    walk is rescaled (R14): its strongest episode gets the walk's strongest seed, the rest proportionally, since a
    thing divides its share by its degree (hundreds on the real graph)."""
    floor, n = float(cfg["floor"]), int(cfg["expose_items"])
    lit = {r["item"]: r["activation"] for r in mind.top(db, chat_id, lane, _POOL, floor + _EPS)
           if r["kind"] == "amintire"}
    for seeds in ({t: 1.0 for t in things.values() if t}, lit):
        got = recall.spread(db, seeds, now, n, 3, half) if seeds else []
        act_max = max((c["activation"] for c in got), default=0.0)
        for c in got if act_max > 0 else []:
            amount = max(seeds.values()) * c["activation"] / act_max
            if amount > floor:
                why = f"legat de: {c['via']}" if c["via"] else "legat de ce e în minte"
                mind.light(db, chat_id, lane, c["id"], "amintire", amount, "asociere", why, now)


def _gather(db, chat_id: str, lane: str, things: dict, cfg: dict, now: float) -> tuple[list[dict], list, dict, dict]:
    """Light the open items, then read what is in mind: (amintiri, open items shown [(id, text, ts, why)] most recent
    first, meta {id: (gist, ts)}, about {thing id: episode ids}). At most expose_items // 3 open items (R14); the
    memories fill the rest of expose_items."""
    floor, n = float(cfg["floor"]), int(cfg["expose_items"])
    rows = db.execute("SELECT id, kind, text, ts FROM open_items WHERE chat_id = ? AND lane = ? "
                      "AND closed_ts IS NULL ORDER BY ts DESC", (chat_id, lane)).fetchall()
    for oid, kind, _, _ in rows:
        mind.light(db, chat_id, lane, oid, "deschis", 1.0, "deschis", kind or "deschis", now)
    opens = [(oid, text, ts, kind or "deschis") for oid, kind, text, ts in rows[:n // 3]]
    items = [it for it in mind.top(db, chat_id, lane, _POOL, floor + _EPS) if it["kind"] == "amintire"]
    items = items[:max(0, n - len(opens))]
    meta = {}
    for it in items:
        gist = db.execute("SELECT gist FROM episode_meta WHERE id = ?", (it["item"],)).fetchone()
        node = db.execute("SELECT ts FROM graph_nodes WHERE id = ?", (it["item"],)).fetchone()
        meta[it["item"]] = ((gist[0] if gist else None) or "", node[0] if node else None)
    about = {t: {r[0] for r in db.execute("SELECT src FROM graph_edges WHERE dst = ? AND kind = 'despre'", (t,))}
             for t in things.values() if t}
    return items, opens, meta, about


def _render(facts: dict, mems: list[dict], opens: list[str], cues: list[tuple[str, set]] | None,
            budget: int) -> tuple[str, list[str], dict[str, str]]:
    """The block within ``budget`` tokens. ``mems`` strongest first, ``opens`` most recent first, ``cues`` the
    (label, episodes about it) to check when the message asks about the past (None otherwise). Trimmed in this
    order: memory lines without an excerpt (weakest first), raw excerpts, the remaining memory lines, open lines
    (oldest first), missing lines; ``Ce știu`` never. A cue is missing when none of the memories LEFT is about it."""
    keep, raw, opens = list(mems), {m["id"] for m in mems[:_RAW_N] if m["raw"]}, list(opens)
    miss_cap = len(cues or [])

    def build() -> str:
        parts = ["[În minte]"]
        if facts:
            parts.append("Ce știu:\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()))
        if keep:
            lines = []
            for m in keep:
                lines.append(f"- ({m['date']}) {m['gist']} — {m['why']}")
                if m["id"] in raw:
                    lines.append(f"  „{m['raw']}”")
            parts.append("Ce s-a întâmplat:\n" + "\n".join(lines))
        if opens:
            parts.append("Ce e deschis:\n" + "\n".join(opens))
        ids = {m["id"] for m in keep}
        missing = [lb for lb, eps in cues or [] if not (eps & ids)][:miss_cap]
        if missing:
            parts.append("Ce nu știu:\n" + "\n".join(f"- nu ai nimic despre {x}" for x in missing))
        return "\n\n".join(parts) if len(parts) > 1 else ""

    block = build()
    while block and estimate_tokens(block) > budget:
        plain = [m for m in keep if m["id"] not in raw]
        if plain:
            keep.remove(plain[-1])
        elif raw:
            raw.discard(next(m["id"] for m in reversed(keep) if m["id"] in raw))
        elif keep:
            keep.pop()
        elif opens:
            opens.pop()
        elif miss_cap:
            miss_cap -= 1
        else:
            break
        block = build()
    texts = {m["id"]: m["gist"] + ("\n" + m["raw"] if m["id"] in raw else "") for m in keep}
    return block, [m["id"] for m in keep], texts


async def _memories(episodic, mems: list[dict], hits: dict, meta: dict) -> list[dict]:
    """The memories to show, strongest first: text and date from the search hit or the episodic store, gist from
    episode_meta (else the text's start); a raw excerpt only when it says more than the gist."""
    found = {mid: (r.get("content") or "", (r.get("metadata") or {}).get("timestamp")) for mid, r in hits.items()}
    need = [it["item"] for it in mems if it["item"] not in found]
    if need:
        got = await asyncio.to_thread(episodic._get_collection().get, ids=need, include=["documents", "metadatas"])
        for i, d, m in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
            found[i] = (d or "", (m or {}).get("timestamp"))
    out = []
    for it in mems:
        text, ts = found.get(it["item"], ("", None))
        gist, node_ts = meta.get(it["item"], ("", None))
        if not (text or gist):
            continue  # a memory the store no longer has: nothing to show
        raw = text[:_RAW_MAX] if gist or len(text) > _GIST_MAX else ""
        out.append({"id": it["item"], "date": _day(ts if ts is not None else node_ts), "why": it["why"],
                    "gist": (gist or text[:_GIST_MAX]).replace("\n", " "), "raw": raw})
    return out


async def expose(client, episodic, chat_id: str, lane: str, message: str, l3_results: list[dict], facts: dict,
                 budget_tokens: int) -> tuple[str, list[str], dict[str, str]]:
    """(the ``[În minte]`` block, ids of the memories shown, {id: text shown}); ("", [], {}) on any error."""
    try:
        brain, cfg, now = _brain.get_instance(), _brain.MIND, time.time()
        half = float(_brain.CONFIG.get("graph_half_life_days", 14.0))
        hits = {_mid(r): r for r in sorted(l3_results or [], key=lambda r: float(r.get("blended_score") or 0.0))
                if _mid(r)}  # a memory found twice keeps its best score

        def _before() -> list[dict]:
            mind.carry(brain._db, chat_id, lane, float(cfg["carry"]), float(cfg["floor"]))
            for mid, r in hits.items():
                mind.light(brain._db, chat_id, lane, mid, "amintire", max(0.0, float(r.get("blended_score") or 0.0)),
                           "căutare", "seamănă cu ce spui", now)
            in_mind = mind.top(brain._db, chat_id, lane, int(cfg["expose_items"]), float(cfg["floor"]) + _EPS)
            return [dict(it, text=str((hits.get(it["item"]) or {}).get("content") or "")) for it in in_mind]

        async with brain.lock:
            in_mind = await asyncio.to_thread(_before)
        cues = await understand_message(client, message, in_mind)  # outside the lock: an LLM call

        def _after():
            things = _things(brain._db, (cues or {}).get("about") or [])
            _associate(brain._db, chat_id, lane, things, cfg, half, now)
            return (things, *_gather(brain._db, chat_id, lane, things, cfg, now))

        async with brain.lock:
            things, items, opens, meta, about = await asyncio.to_thread(_after)
        shown_mems = await _memories(episodic, items, hits, meta)
        open_lines = [f"- ({_day(ts)}) {text} — {why}" for _, text, ts, why in opens]
        asks = [(lb, about.get(tid, set()) if tid else set()) for lb, tid in things.items()] \
            if cues and cues.get("asks_past") else None
        return _render(facts or {}, shown_mems, open_lines, asks, int(budget_tokens))
    except Exception as exc:  # noqa: BLE001
        log.warning("brain: mind expose failed: %s: %s", type(exc).__name__, exc)
        return "", [], {}
