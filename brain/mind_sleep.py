"""brain.mind_sleep — the existing L3 history goes through the same understanding + encoding as new turns.

Each sleep tick takes the newest episodes without an ``episode_meta`` row (all lanes: the recent memories matter
most and are the ones seen soonest, P4), understands each with the previous episodes of its topic as ``in_mind``
(no topic: the previous ones of its chat within 30 minutes, R25) and the labels of the things they are about (R28);
a neighbour with no gist yet is read by its cleaned raw text, fetched in the slice's one Chroma call (P4). It
encodes it (rulings R3, R6, R22). Chroma and the LLM run outside
``brain.lock``; the lock is held only for DB reads/writes. An episode whose understanding fails is skipped for an
hour (in-process), and after 3 failures not retried again in this process (R34), so one bad episode never blocks the
slice. Nothing is ever deleted.
"""
from __future__ import annotations

import asyncio
import re
import time

from brain import brain as _brain
from brain import encode
from brain.live import _NOT_LIVED
from brain.mind_render import clean
from brain.understand import understand_turn
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["encode_slice"]

_RETRY_AFTER = 3600.0
_MAX_FAILS = 3
_RECENT = 24 * 3600.0
_CHAT_WINDOW = 30 * 60.0  # no topic: the episodes of the same chat this long before are what was in mind
_failed: dict[str, float] = {}
_fails: dict[str, int] = {}
_NEIGHBOUR_MAX = 160  # characters of a neighbour's raw text when it has no gist yet
_PREFIX = re.compile(r"^\[[^\]]{0,40}\]\s*")


def _split(doc: str) -> tuple[str, str]:
    """(user, reply) of an L3 doc: split at the first "assistant:"; a leading "[timestamp] " and "user:" dropped."""
    doc = doc or ""
    head, sep, tail = doc.partition("\nassistant:")
    if not sep and doc.startswith("assistant:"):
        head, sep, tail = doc.partition("assistant:")
    user = _PREFIX.sub("", head.strip(), count=1)
    if user.startswith("user:"):
        user = user[5:].strip()
    return user, tail.strip() if sep else ""


def _lane(user: str) -> str:
    return "channel" if user.lstrip().startswith(_NOT_LIVED) else "human"


def _in_mind(db, chat_id, topic_id, ts: float) -> tuple[list[dict], list[str]]:
    """(the up to 8 episodes before this one in its topic, or in its chat within _CHAT_WINDOW when it has no topic,
    oldest first, with their gist; the labels of the things they are about)."""
    if topic_id is not None:
        rows = db.execute("SELECT n.id, m.gist FROM graph_nodes n LEFT JOIN episode_meta m ON m.id = n.id "
                          "WHERE n.kind = 'episode' AND n.topic_id = ? AND n.ts < ? ORDER BY n.ts DESC LIMIT 8",
                          (topic_id, ts)).fetchall()
    else:
        rows = db.execute("SELECT n.id, m.gist FROM graph_nodes n LEFT JOIN episode_meta m ON m.id = n.id "
                          "WHERE n.kind = 'episode' AND n.chat_id IS ? AND n.ts < ? AND n.ts >= ? "
                          "ORDER BY n.ts DESC LIMIT 8", (chat_id, ts, ts - _CHAT_WINDOW)).fetchall()
    return ([{"item": r[0], "kind": "amintire", "text": r[1] or ""} for r in reversed(rows)],
            encode.thing_labels(db, [r[0] for r in rows]))


def _fail(eid: str) -> None:
    _failed[eid] = time.time()
    _fails[eid] = _fails.get(eid, 0) + 1


def _pending(db, n: int) -> list[tuple]:
    return db.execute("SELECT n.id, n.chat_id, n.topic_id, n.ts FROM graph_nodes n WHERE n.kind = 'episode' "
                      "AND n.ts IS NOT NULL AND NOT EXISTS (SELECT 1 FROM episode_meta m WHERE m.id = n.id) "
                      "ORDER BY n.ts DESC LIMIT ?", (n,)).fetchall()


async def encode_slice(brain, episodic, client, limit: int | None = None, user_name: str | None = None) -> int:
    """Encode up to ``limit`` of the newest unencoded episodes; returns how many were encoded. ``user_name`` (L1)
    is how the gists name the user, whatever the old turns call him (P3)."""
    limit = int(limit if limit is not None else _brain.MIND["encode_per_tick"])
    now = time.time()
    for i in [i for i, t in _failed.items() if now - t >= _RETRY_AFTER and _fails.get(i, 0) < _MAX_FAILS]:
        del _failed[i]
    async with brain.lock:
        rows = await asyncio.to_thread(_pending, brain._db, limit + len(_failed))
    rows = [r for r in rows if r[0] not in _failed][:limit]
    if not rows:
        return 0
    async with brain.lock:  # each one's neighbours, read before the slice is encoded: they are older, so newest
        #                     first they would be encoded after it anyway
        minds = await asyncio.to_thread(lambda: {r[0]: _in_mind(brain._db, r[1], r[2], r[3]) for r in rows})
    bare = [it["item"] for m, _ in minds.values() for it in m if not it["text"]]
    got = await asyncio.to_thread(episodic._get_collection().get,
                                  ids=list(dict.fromkeys([r[0] for r in rows] + bare)), include=["documents"])
    docs = dict(zip(got.get("ids") or [], got.get("documents") or []))
    done = 0
    for eid, chat_id, topic_id, ts in rows:
        if not docs.get(eid):
            _fail(eid)
            continue
        user, reply = _split(docs[eid])
        mind, labels = minds[eid]
        mind = [it if it["text"] else {**it, "text": clean(docs.get(it["item"]) or "")[:_NEIGHBOUR_MAX]}
                for it in mind]
        try:
            u = await understand_turn(client, user, reply, mind, labels, user_name=user_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("brain: backfill understand failed %s: %s: %s", eid, type(exc).__name__, exc)
            u = None
        if u is None:
            _fail(eid)
            continue
        if time.time() - ts > _RECENT:  # R3: old history never re-opens promises
            u = {**u, "opens": None, "closes": []}
        try:
            async with brain.lock:
                await asyncio.to_thread(encode.encode, brain._db, str(chat_id), _lane(user), eid, ts, u, time.time())
        except Exception as exc:  # noqa: BLE001 - one bad episode must not block the slice
            log.warning("brain: backfill encode failed %s: %s: %s", eid, type(exc).__name__, exc)
            _fail(eid)
            continue
        done += 1
    if done:
        log.info("brain: backfill encoded=%d of %d", done, len(rows))
    return done
