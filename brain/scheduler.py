"""brain.scheduler — scheduled messages on the brain's own tick (Laris + GOAT, 2026-11-15).

A task is "send ``text`` to ``chat_id`` at ``run_at``". Tasks live in the brain's SQLite file (table
``scheduled_tasks``), so a restart loses none. Not a cron, not a separate process: the sleep loop calls ``run_due``
once per tick (``interval_seconds``, 60 s), so a task goes out at most one tick late (±1 min).

Delivery is at-least-once: a task is marked ``sending`` (attempts + 1) BEFORE the send and ``sent`` after it; a
process that dies in between leaves it ``sending``, and the next start puts it back to ``pending``. A failed send
is retried on the next ticks; after ``max_attempts`` it is ``failed`` and stays in the table (never deleted).
All SQLite access goes through the shared Brain's connection, under its lock, in a thread; the send itself (network)
runs outside the lock, so a slow Telegram never holds the store.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["SCHEMA", "schedule", "pending", "run_due", "recover"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
    run_at REAL NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL
    DEFAULT 0, created_at REAL NOT NULL, sent_at REAL, last_error TEXT);
CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled_tasks (status, run_at);
"""

Send = Callable[[str, str], Awaitable[None]]


def _ensure(db) -> None:
    db.executescript(SCHEMA)


async def schedule(brain, chat_id: str | int, run_at: float, text: str) -> int:
    """Register one message; returns its id. ``run_at`` is a unix time; one in the past goes out at the next tick."""
    if not str(text or "").strip():
        raise ValueError("a scheduled message needs a text")

    def _add() -> int:
        _ensure(brain._db)
        cur = brain._db.execute("INSERT INTO scheduled_tasks (chat_id, run_at, text, created_at) VALUES (?, ?, ?, ?)",
                                (str(chat_id), float(run_at), str(text), time.time()))
        brain._db.commit()
        return int(cur.lastrowid)
    async with brain.lock:
        return await asyncio.to_thread(_add)


async def pending(brain, chat_id: str | int | None = None) -> list[dict]:
    """The tasks not yet sent (pending or sending), soonest first."""
    def _list() -> list[dict]:
        _ensure(brain._db)
        sql = "SELECT id, chat_id, run_at, text, status, attempts FROM scheduled_tasks WHERE status IN ('pending', 'sending')"
        args: tuple = ()
        if chat_id is not None:
            sql, args = sql + " AND chat_id = ?", (str(chat_id),)
        rows = brain._db.execute(sql + " ORDER BY run_at", args).fetchall()
        return [dict(zip(("id", "chat_id", "run_at", "text", "status", "attempts"), r)) for r in rows]
    async with brain.lock:
        return await asyncio.to_thread(_list)


async def recover(brain) -> int:
    """At start: tasks left ``sending`` by a process that died mid-send go back to ``pending`` (at-least-once)."""
    def _fix() -> int:
        _ensure(brain._db)
        n = brain._db.execute("UPDATE scheduled_tasks SET status = 'pending' WHERE status = 'sending'").rowcount
        brain._db.commit()
        return n
    async with brain.lock:
        return await asyncio.to_thread(_fix)


async def run_due(brain, send: Send, now: float | None = None, max_attempts: int = 3, limit: int = 20) -> dict:
    """One tick: claim the due tasks (at most ``limit``), send each through ``send(chat_id, text)``, mark the result.
    A failing send never stops the others; returns the counts (due / sent / retry / failed / ms)."""
    t0, now = time.perf_counter(), time.time() if now is None else now

    def _claim() -> list[tuple]:
        _ensure(brain._db)
        rows = brain._db.execute("SELECT id, chat_id, text, attempts FROM scheduled_tasks WHERE status = 'pending' AND "
                                 "run_at <= ? ORDER BY run_at LIMIT ?", (now, int(limit))).fetchall()
        brain._db.executemany("UPDATE scheduled_tasks SET status = 'sending', attempts = attempts + 1 WHERE id = ?",
                              [(r[0],) for r in rows])
        brain._db.commit()
        return rows
    async with brain.lock:
        due = await asyncio.to_thread(_claim)
    if not due:
        return {"due": 0, "sent": 0, "retry": 0, "failed": 0, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    results: list[tuple[int, str, str | None]] = []  # (id, new status, error)
    for tid, chat_id, text, attempts in due:
        try:
            await send(chat_id, text)
            results.append((tid, "sent", None))
        except Exception as exc:  # noqa: BLE001 - one bad task never blocks the rest
            status = "failed" if attempts + 1 >= max_attempts else "pending"
            results.append((tid, status, f"{type(exc).__name__}: {exc}"[:300]))
            log.warning("brain: scheduled task %d send failed (attempt %d/%d): %s", tid, attempts + 1, max_attempts, exc)

    def _mark() -> None:
        stamp = time.time()
        brain._db.executemany("UPDATE scheduled_tasks SET status = ?, last_error = ?, sent_at = CASE WHEN ? = 'sent' "
                              "THEN ? ELSE sent_at END WHERE id = ?",
                              [(st, err, st, stamp, tid) for tid, st, err in results])
        brain._db.commit()
    async with brain.lock:
        await asyncio.to_thread(_mark)
    counts = {k: sum(1 for _, st, _ in results if st == v) for k, v in (("sent", "sent"), ("retry", "pending"),
                                                                        ("failed", "failed"))}
    out = {"due": len(due), **counts, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    log.info("brain: scheduler due=%d sent=%d retry=%d failed=%d ms=%.1f", out["due"], out["sent"], out["retry"],
             out["failed"], out["ms"])
    return out
