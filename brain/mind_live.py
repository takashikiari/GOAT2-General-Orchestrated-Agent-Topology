"""brain.mind_live — the working memory ("În minte") inside a turn (spec 2026-11-21 §2 F1, F4, F7).

``expose``, before the reply: what was in mind fades; search hits, the message's cues (spreading along the typed graph
links to memories the search never returned) and the open items light ONE working memory; its top becomes one
Romanian block — what is known, what happened (each with why), what is open, what is missing. Any error: logged, the
turn goes on without the block. Nothing is ever deleted (brain/mind.py).
"""
from __future__ import annotations

import asyncio
import time

from brain import brain as _brain
from brain import encode as _encode
from brain import graph, mind, mind_facts, recall
from brain.live import used as _used
from brain.mind_render import day as _day
from brain.mind_render import memories as _memories
from brain.mind_render import render as _render
from brain.understand import understand_message, understand_turn
from memory.usage_probe import _terms
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["expose", "after_turn"]

_EPS = 1e-9  # "above floor": an item faded to the floor is no longer in mind
_MIND_MEMS, _MIND_OPENS = 8, 4  # what the understanding reads as in mind: memories first, open items capped (R30)
_POOL = 200  # amintire items read as spread seeds
_ASSOC_CEILING = 0.8  # share of a walk's strongest seed its strongest episode gets (R16): the graph alone stays
#                       below what search and graph light together, so convergence ranks first


def _mid(r: dict) -> str:
    return str((r.get("metadata") or {}).get("message_id") or r.get("id") or "")


def _with_text(db, chat_id: str, lane: str, items: list[dict], texts: dict[str, str] | None) -> list[dict]:
    """``items`` with the line the model should read: ``texts`` when present, else an open item's text (this chat and
    lane), else a memory's gist; empty when nothing is known."""
    out = []
    for it in items:
        text = (texts or {}).get(it["item"]) or ""
        if not text and it["kind"] == "deschis":
            row = db.execute("SELECT text FROM open_items WHERE id = ? AND chat_id = ? AND lane = ?",
                             (it["item"], chat_id, lane)).fetchone()
            text = (row[0] if row else "") or ""
        elif not text and it["kind"] == "amintire":
            row = db.execute("SELECT gist FROM episode_meta WHERE id = ?", (it["item"],)).fetchone()
            text = (row[0] if row else "") or ""
        out.append(dict(it, text=text))
    return out


def _missing(db, names: list[str], facts: dict | None = None) -> list[str]:
    """The named things the graph knows nothing about (R24, R27): no thing node, or no episode ``despre`` it at all.
    A thing with memories is never missing, whether or not they are lit, shown or trimmed this turn; nor is a name
    an L1 fact mentions (F7)."""
    return [lb for lb, tid in _things(db, names).items() if not mind_facts.mentioned(facts or {}, lb)
            and (tid is None or not db.execute("SELECT 1 FROM graph_edges WHERE dst = ? AND kind = 'despre' LIMIT 1",
                                             (tid,)).fetchone())]


def _in_mind(db, chat_id: str, lane: str, floor: float, texts: dict[str, str] | None) -> list[dict]:
    """What the understanding reads as in mind: up to _MIND_MEMS memories, then up to _MIND_OPENS open items (R30),
    strongest first, each with its text."""
    rows = mind.top(db, chat_id, lane, _POOL, floor + _EPS)
    mems = [r for r in rows if r["kind"] == "amintire"][:_MIND_MEMS]  # facts in mind are not links to make
    return _with_text(db, chat_id, lane, mems + [r for r in rows if r["kind"] == "deschis"][:_MIND_OPENS], texts)


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
    walk is rescaled (R14, R16): its strongest episode gets _ASSOC_CEILING x the walk's strongest seed, the rest
    proportionally, since a thing divides its share by its degree (hundreds on the real graph)."""
    floor, n = float(cfg["floor"]), int(cfg["expose_items"])
    for walk in ("cues", "mind"):  # the memories in mind are read AFTER the cue walk: what it lit seeds, not gains
        seeds = {t: 1.0 for t in things.values() if t} if walk == "cues" else \
            {r["item"]: r["activation"] for r in mind.top(db, chat_id, lane, _POOL, floor + _EPS)
             if r["kind"] == "amintire"}
        got = recall.spread(db, seeds, now, n, 3, half) if seeds else []
        act_max = max((c["activation"] for c in got), default=0.0)
        for c in got if act_max > 0 else []:
            amount = _ASSOC_CEILING * max(seeds.values()) * c["activation"] / act_max
            if amount > floor:
                why = f"legat de: {c['via']}" if c["via"] else "legat de ce e în minte"
                mind.light(db, chat_id, lane, c["id"], "amintire", amount, "asociere", why, now, commit=False)


def _gather(db, chat_id: str, lane: str, cfg: dict, now: float, exclude: set[str]) -> tuple[list[dict], list, dict]:
    """Light the open items, then read what is in mind: (amintiri, open items shown [(id, text, ts, why)] most recent
    first, meta {id: (gist, ts)}). Only open items younger than [brain] inject_promise_max_hours are lit, at most
    expose_items // 3 (R14, R30): older unclosed ones keep their row, unlit. The memories fill the rest of
    expose_items; those in ``exclude`` (the turns of the conversation already in the prompt, R31) are not shown."""
    floor, n = float(cfg["floor"]), int(cfg["expose_items"])
    since = now - float(_brain.CONFIG.get("inject_promise_max_hours", 24)) * 3600.0
    rows = db.execute("SELECT id, kind, text, ts FROM open_items WHERE chat_id = ? AND lane = ? AND closed_ts IS NULL "
                      "AND ts >= ? ORDER BY ts DESC LIMIT ?", (chat_id, lane, since, n // 3)).fetchall()
    for oid, kind, _, _ in rows:
        mind.light(db, chat_id, lane, oid, "deschis", 1.0, "deschis", kind or "deschis", now, commit=False)
    opens = [(oid, text, ts, kind or "deschis") for oid, kind, text, ts in rows]
    items = [it for it in mind.top(db, chat_id, lane, _POOL, floor + _EPS)
             if it["kind"] == "amintire" and it["item"] not in exclude]
    items = items[:max(0, n - len(opens))]
    meta = {}
    for it in items:
        gist = db.execute("SELECT gist FROM episode_meta WHERE id = ?", (it["item"],)).fetchone()
        node = db.execute("SELECT ts FROM graph_nodes WHERE id = ?", (it["item"],)).fetchone()
        meta[it["item"]] = ((gist[0] if gist else None) or "", node[0] if node else None)
    return items, opens, meta


async def expose(client, episodic, chat_id: str, lane: str, message: str, l3_results: list[dict], facts: dict,
                 budget_tokens: int, *, exclude_ids: set[str] | None = None) -> tuple[str, list[str], dict[str, str]]:
    """(the ``[În minte]`` block, ids of the memories shown, {id: text shown}); ("", [], {}) on any error.
    ``exclude_ids``: the episodes already in the prompt as conversation (the L2 turns' l3_ids), never shown (R31)."""
    try:
        brain, cfg, now = _brain.get_instance(), _brain.MIND, time.time()
        half = float(_brain.CONFIG.get("graph_half_life_days", 14.0))
        exclude = set(exclude_ids or ())
        hits = {_mid(r): r for r in sorted(l3_results or [], key=lambda r: float(r.get("blended_score") or 0.0))
                if _mid(r) and _mid(r) not in exclude}  # a memory found twice keeps its best score

        def _before() -> tuple[list[dict], dict]:
            mind.carry(brain._db, chat_id, lane, float(cfg["carry"]), float(cfg["floor"]))
            for mid, r in hits.items():
                mind.light(brain._db, chat_id, lane, mid, "amintire", max(0.0, float(r.get("blended_score") or 0.0)),
                           "căutare", "seamănă cu ce spui", now, commit=False)
            brain._db.commit()
            return (_in_mind(brain._db, chat_id, lane, float(cfg["floor"]),
                             {m: str(r.get("content") or "") for m, r in hits.items()}), mind_facts.kinds(brain._db))

        async with brain.lock:
            in_mind, kinds = await asyncio.to_thread(_before)
        rules, knowledge = mind_facts.split(facts or {}, kinds)  # P1: rules always, knowledge when it is needed
        cues = await understand_message(client, message, in_mind, knowledge)  # outside the lock: an LLM call

        def _after():
            _associate(brain._db, chat_id, lane, _things(brain._db, (cues or {}).get("about") or []), cfg, half, now)
            mind_facts.light(brain._db, chat_id, lane, (cues or {}).get("facts") or [], now)
            got = _gather(brain._db, chat_id, lane, cfg, now, exclude)
            brain._db.commit()
            missing = _missing(brain._db, cues.get("names") or [], facts) if cues and cues.get("asks_past") else []
            return (*got, missing, mind_facts.in_mind(brain._db, chat_id, lane, knowledge))

        async with brain.lock:
            items, opens, meta, missing, known = await asyncio.to_thread(_after)
        shown_mems = await _memories(episodic, items, hits, meta)
        open_lines = [f"- ({_day(ts)}) {text} — {why}" for _, text, ts, why in opens]
        return _render(known, shown_mems, open_lines, missing, int(budget_tokens), rules)
    except Exception as exc:  # noqa: BLE001
        log.warning("brain: mind expose failed: %s: %s", type(exc).__name__, exc)
        return "", [], {}


async def after_turn(client, chat_id: str, lane: str, turn_id: str, user: str, reply: str, shown: list[str],
                     texts: dict[str, str], ts: float | None = None) -> None:
    """After the reply (background, never raises): what the reply used stays lit, the rest fades; the turn is linked
    to what was shown (0.2 used / 0.05 present); the turn is understood against what is in mind and encoded at its
    own time ``ts`` (when it was said; default now, R36). When the understanding fails the turn stays unencoded (no
    episode_meta row) for the backfill."""
    try:
        brain, cfg, now = _brain.get_instance(), _brain.MIND, time.time()
        rterms = _terms(reply or "")
        was_used = {m: _used((texts or {}).get(m, ""), rterms) for m in shown or []}

        def _learn() -> tuple[list[dict], list[str]]:
            mind.mark(brain._db, chat_id, lane, was_used, now, float(cfg["floor"]))
            for m, u in was_used.items():
                graph.touch(brain._db, turn_id, m, "gândite_împreună", 0.2 if u else 0.05, now)
            brain._db.commit()
            top = _in_mind(brain._db, chat_id, lane, float(cfg["floor"]), texts)
            return top, _encode.thing_labels(brain._db, [it["item"] for it in top if it["kind"] != "deschis"])

        async with brain.lock:
            in_mind, labels = await asyncio.to_thread(_learn)
        understood = await understand_turn(client, user, reply, in_mind, labels)  # outside the lock: an LLM call
        if understood is None:
            return
        async with brain.lock:
            done = await asyncio.to_thread(_encode.encode, brain._db, chat_id, lane, turn_id, ts or now, understood,
                                           now)
        log.info("brain: mind after_turn used=%d shown=%d encoded=%s", sum(was_used.values()), len(was_used), done)
    except Exception as exc:  # noqa: BLE001
        log.warning("brain: mind after_turn failed: %s: %s", type(exc).__name__, exc)
