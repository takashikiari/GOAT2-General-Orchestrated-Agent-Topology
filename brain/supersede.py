"""brain.supersede — a correction replaces the old memory (spec 2026-11-21 §2 F5, §5; corrections-fixes C1-C3).

When a turn is encoded with a ``corectează`` / ``contrazice`` link to an earlier episode X, X is superseded by it:
``episode_meta.superseded_by`` / ``superseded_at`` are set and X fades to the floor in this chat's working memory.
Exposure then never shows X as current: whoever reaches X (the search, the prefetch cache, the graph) lights the
newest episode of its chain instead, and that episode's line says what it replaced. The old gist is never changed and
nothing is ever deleted: X keeps its row, its links and its text, as history.

Chains: X <- E1 <- E2 (E2 corrected E1, which corrected X); the head is the newest episode not superseded. A
correction of an already superseded memory corrects its CURRENT version (the head), so the chain grows and no pointer
is rewired. An episode never supersedes a later one (by the episode's own time) nor anything already downstream of
it, so no cycle is written; ``head`` still stops on a cycle written by other means.
"""
from __future__ import annotations

__all__ = ["CORRECTING", "WHY", "mark", "restore", "head", "superseded", "history", "replaced"]

CORRECTING = frozenset({"corectează", "contrazice"})
WHY = "versiunea corectată, actuală"  # why the newest version is in mind when an older one was reached
_REPLACED_MAX = 80
_MAX_CHAIN = 64  # a chain longer than this is not a chain of corrections: stop walking


def _next(db, eid: str) -> str | None:
    row = db.execute("SELECT superseded_by FROM episode_meta WHERE id = ?", (eid,)).fetchone()
    return row[0] if row and row[0] else None


def _chain(db, eid: str) -> list[str]:
    """``eid`` and every episode after it in its supersede chain, oldest first; stops on a cycle."""
    out, cur = [eid], _next(db, eid)
    while cur and cur not in out and len(out) < _MAX_CHAIN:
        out.append(cur)
        cur = _next(db, cur)
    return out


def head(db, eid: str) -> str:
    """The newest version of ``eid``: the last episode of its supersede chain (``eid`` itself when not superseded)."""
    return _chain(db, str(eid))[-1]


def superseded(db, ids) -> set[str]:
    """The ids among ``ids`` that a later episode superseded."""
    ids = [str(i) for i in ids or []]
    out: set[str] = set()
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out.update(r[0] for r in db.execute(
            f"SELECT id FROM episode_meta WHERE superseded_by IS NOT NULL AND id IN ({','.join('?' * len(part))})",
            part))
    return out


def _ts(db, eid: str) -> float | None:
    row = db.execute("SELECT ts FROM graph_nodes WHERE id = ?", (eid,)).fetchone()
    return row[0] if row else None


def mark(db, chat_id: str, old: str, new: str, now: float, floor: float | None = None) -> str | None:
    """``new`` supersedes the current version of ``old``; that version fades to ``floor`` in every lane of this chat.
    No commit: it runs inside encode's transaction. Returns the id superseded; None (nothing written) when it would
    make a cycle or make an episode supersede a later one."""
    if floor is None:
        from brain import brain as _brain  # late: brain.brain imports the store, which imports this package
        floor = float(_brain.MIND.get("floor", 0.001))
    target = head(db, str(old))
    if target == new or target in _chain(db, new):
        return None
    t_old, t_new = _ts(db, target), _ts(db, new)
    if t_old is not None and t_new is not None and t_old > t_new:
        return None
    db.execute("INSERT INTO episode_meta (id, superseded_by, superseded_at) VALUES (?, ?, ?) "
               "ON CONFLICT(id) DO UPDATE SET superseded_by = excluded.superseded_by, "
               "superseded_at = excluded.superseded_at", (target, new, now))
    db.execute("UPDATE mind SET activation = MIN(activation, ?), updated = ? WHERE chat_id = ? AND item = ?",
               (floor, now, chat_id, target))
    return target


def restore(db, episode_id: str) -> bool:
    """Undo a wrong supersede (e.g. a ``contrazice`` on a mere disagreement): ``episode_id`` is current again. Only
    its ``superseded_by`` / ``superseded_at`` are cleared (UPDATE; the correcting edge and every row stay). Its
    activation is not raised: the next cue brings it back. False when it was not superseded or is unknown. Manual
    use only: no tool and no automatic path calls it."""
    cur = db.execute("UPDATE episode_meta SET superseded_by = NULL, superseded_at = NULL "
                     "WHERE id = ? AND superseded_by IS NOT NULL", (str(episode_id),))
    db.commit()
    return cur.rowcount > 0


def history(db, eid: str) -> list[tuple[str, str, float | None]]:
    """Every version of the memory ``eid`` belongs to, oldest to newest: [(id, gist, ts)]. The versions before the
    head are the ones it replaced (a correction may replace several); the order is by the episode's own time."""
    top = head(db, str(eid))
    seen, todo = {top}, [top]
    while todo and len(seen) < _MAX_CHAIN:
        cur = todo.pop()
        for (prev,) in db.execute("SELECT id FROM episode_meta WHERE superseded_by = ?", (cur,)).fetchall():
            if prev not in seen:
                seen.add(prev)
                todo.append(prev)
    rows = []
    for i in seen:
        g = db.execute("SELECT gist FROM episode_meta WHERE id = ?", (i,)).fetchone()
        rows.append((i, (g[0] if g else None) or "", _ts(db, i)))
    rows.sort(key=lambda r: (r[0] == top, r[2] if r[2] is not None else float("-inf"), r[0]))  # the head last
    return rows


def replaced(db, eid: str) -> tuple[str, float | None] | None:
    """What ``eid`` replaced, for its line in ``[În minte]`` (reconsolidation, F5): (the previous version's gist,
    at most _REPLACED_MAX characters, its time); None when ``eid`` replaced nothing or that version has no gist."""
    versions = history(db, eid)
    if len(versions) < 2 or versions[-1][0] != str(eid):
        return None
    _, gist, ts = versions[-2]
    gist = " ".join((gist or "").split())
    if not gist:
        return None
    return (gist if len(gist) <= _REPLACED_MAX else gist[:_REPLACED_MAX].rstrip() + "…"), ts
