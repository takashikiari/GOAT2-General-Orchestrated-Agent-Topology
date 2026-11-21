"""brain.mind_sleep — the existing L3 history goes through the same understanding + encoding as new turns.

Each sleep tick takes the oldest episodes without an ``episode_meta`` row (all lanes), understands each with the
previous episodes of its topic as ``in_mind``, and encodes it (rulings R3, R6, R22). Chroma and the LLM run outside
``brain.lock``; the lock is held only for DB reads/writes. An episode whose understanding fails is skipped for an
hour (in-process), so one bad episode never blocks the oldest-first slice. Nothing is ever deleted.
"""
from __future__ import annotations

import asyncio
import re
import time

from brain import brain as _brain
from brain import encode
from brain.live import _NOT_LIVED
from brain.understand import understand_turn
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["encode_slice"]

_RETRY_AFTER = 3600.0
_RECENT = 24 * 3600.0
_failed: dict[str, float] = {}
_PREFIX = re.compile(r"^\[[^\]]{0,40}\]\s*")


def _split(doc: str) -> tuple[str, str]:
    """(user, reply) of an L3 doc: split at the first "assistant:"; a leading "[timestamp] " and "user:" dropped."""
    head, sep, tail = (doc or "").partition("assistant:")
    user = _PREFIX.sub("", head.strip(), count=1)
    if user.startswith("user:"):
        user = user[5:].strip()
    return user, tail.strip() if sep else ""


def _lane(user: str) -> str:
    return "channel" if user.lstrip().startswith(_NOT_LIVED) else "human"


def _in_mind(db, topic_id, ts: float) -> list[dict]:
    if topic_id is None:
        return []
    rows = db.execute("SELECT n.id, m.gist FROM graph_nodes n LEFT JOIN episode_meta m ON m.id = n.id "
                      "WHERE n.kind = 'episode' AND n.topic_id = ? AND n.ts < ? ORDER BY n.ts DESC LIMIT 8",
                      (topic_id, ts)).fetchall()
    return [{"item": r[0], "kind": "amintire", "text": r[1] or ""} for r in reversed(rows)]


def _pending(db, n: int) -> list[tuple]:
    return db.execute("SELECT n.id, n.chat_id, n.topic_id, n.ts FROM graph_nodes n WHERE n.kind = 'episode' "
                      "AND n.ts IS NOT NULL AND NOT EXISTS (SELECT 1 FROM episode_meta m WHERE m.id = n.id) "
                      "ORDER BY n.ts LIMIT ?", (n,)).fetchall()


async def encode_slice(brain, episodic, client, limit: int | None = None) -> int:
    """Encode up to ``limit`` of the oldest unencoded episodes; returns how many were encoded."""
    limit = int(limit if limit is not None else _brain.MIND["encode_per_tick"])
    now = time.time()
    for i in [i for i, t in _failed.items() if now - t >= _RETRY_AFTER]:
        del _failed[i]
    async with brain.lock:
        rows = await asyncio.to_thread(_pending, brain._db, limit + len(_failed))
    rows = [r for r in rows if r[0] not in _failed][:limit]
    if not rows:
        return 0
    got = await asyncio.to_thread(episodic._get_collection().get, ids=[r[0] for r in rows], include=["documents"])
    docs = dict(zip(got.get("ids") or [], got.get("documents") or []))
    done = 0
    for eid, chat_id, topic_id, ts in rows:
        if not docs.get(eid):
            _failed[eid] = time.time()
            continue
        user, reply = _split(docs[eid])
        async with brain.lock:
            mind = await asyncio.to_thread(_in_mind, brain._db, topic_id, ts)
        try:
            u = await understand_turn(client, user, reply, mind)
        except Exception as exc:  # noqa: BLE001
            log.warning("brain: backfill understand failed %s: %s: %s", eid, type(exc).__name__, exc)
            u = None
        if u is None:
            _failed[eid] = time.time()
            continue
        if now - ts > _RECENT:  # R3: old history never re-opens promises
            u = {**u, "opens": None, "closes": []}
        async with brain.lock:
            await asyncio.to_thread(encode.encode, brain._db, str(chat_id), _lane(user), eid, ts, u, time.time())
        done += 1
    if done:
        log.info("brain: backfill encoded=%d of %d", done, len(rows))
    return done
