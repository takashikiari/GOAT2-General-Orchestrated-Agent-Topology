"""brain.mind — working memory ("În minte"): one shared state per chat and lane, in brain.sqlite.

Each row is an item (a memory id, a fact, an open item) with an activation in [floor, 1], the sources that lit it
and a short ``why``. Later tasks light items, read the top ones and reinforce the ones the reply used.

Nothing is ever removed: activation only fades down to ``floor`` and the row stays (no row removal in this module).
All functions take the brain's SQLite connection and run inside the brain's lock, in a thread (like brain.store).
"""
from __future__ import annotations

import json

__all__ = ["SCHEMA", "carry", "light", "top", "mark"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS mind (chat_id TEXT NOT NULL, lane TEXT NOT NULL, item TEXT NOT NULL, kind TEXT NOT NULL,
    activation REAL NOT NULL, sources TEXT NOT NULL DEFAULT '[]', why TEXT NOT NULL DEFAULT '', updated REAL,
    last_used REAL, PRIMARY KEY (chat_id, lane, item));
CREATE TABLE IF NOT EXISTS episode_meta (id TEXT PRIMARY KEY, type TEXT, importance REAL, gist TEXT,
    encoded_at REAL);
CREATE TABLE IF NOT EXISTS open_items (id TEXT PRIMARY KEY, chat_id TEXT, lane TEXT, kind TEXT, text TEXT, ts REAL,
    closed_ts REAL, closed_by TEXT);
CREATE TABLE IF NOT EXISTS fact_kind (key TEXT PRIMARY KEY, kind TEXT NOT NULL, classified_at REAL);
"""


def carry(db, chat_id: str, lane: str, factor: float, floor: float) -> int:
    """Fade every item of this chat+lane by ``factor``, never below ``floor``; returns the rows updated."""
    cur = db.execute("UPDATE mind SET activation = MAX(?, activation * ?) WHERE chat_id = ? AND lane = ?",
                     (floor, factor, chat_id, lane))
    db.commit()
    return cur.rowcount


def light(db, chat_id: str, lane: str, item: str, kind: str, amount: float, source: str, why: str,
          now: float, commit: bool = True) -> float:
    """Raise an item's activation by ``amount`` (cap 1), add ``source`` once, keep the latest ``why``. With
    ``commit=False`` the caller commits once after a batch of lights."""
    row = db.execute("SELECT activation, sources FROM mind WHERE chat_id = ? AND lane = ? AND item = ?",
                     (chat_id, lane, item)).fetchone()
    if row is None:
        act, sources = min(1.0, amount), [source]
        db.execute("INSERT INTO mind (chat_id, lane, item, kind, activation, sources, why, updated) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (chat_id, lane, item, kind, act, json.dumps(sources, ensure_ascii=False), why, now))
    else:
        act, sources = min(1.0, row[0] + amount), json.loads(row[1] or "[]")
        if source not in sources:
            sources.append(source)
        db.execute("UPDATE mind SET kind = ?, activation = ?, sources = ?, why = ?, updated = ? "
                   "WHERE chat_id = ? AND lane = ? AND item = ?",
                   (kind, act, json.dumps(sources, ensure_ascii=False), why, now, chat_id, lane, item))
    if commit:
        db.commit()
    return act


def top(db, chat_id: str, lane: str, limit: int, min_activation: float) -> list[dict]:
    """The most active items of this chat+lane (activation >= ``min_activation``), strongest first."""
    rows = db.execute("SELECT item, kind, activation, sources, why FROM mind WHERE chat_id = ? AND lane = ? "
                      "AND activation >= ? ORDER BY activation DESC, updated DESC LIMIT ?",
                      (chat_id, lane, min_activation, limit)).fetchall()
    return [{"item": r[0], "kind": r[1], "activation": r[2], "sources": json.loads(r[3] or "[]"), "why": r[4]}
            for r in rows]


def mark(db, chat_id: str, lane: str, used: dict[str, bool], now: float, floor: float = 0.001) -> None:
    """Reinforce items the reply used (+0.2, cap 1, ``last_used`` = now); fade the shown-but-unused (x0.8)."""
    for item, was_used in used.items():
        if was_used:
            db.execute("UPDATE mind SET activation = MIN(1.0, activation + 0.2), last_used = ?, updated = ? "
                       "WHERE chat_id = ? AND lane = ? AND item = ?", (now, now, chat_id, lane, item))
        else:
            db.execute("UPDATE mind SET activation = MAX(?, activation * 0.8), updated = ? "
                       "WHERE chat_id = ? AND lane = ? AND item = ?", (floor, now, chat_id, lane, item))
    db.commit()
