"""brain.usage — which L3 documents were actually USED, i.e. injected into GOAT's prompt (Laris + GOAT, 2026-11-15).

Phase 0 of the prefetch feedback: the signal only, collected, read by no one yet. Measured on the live store the
same day: no path link was ever reinforced (all weights <= 0.999: a link is written once and only fades), so "events
reinforced on an l3_id" would only be how recent a turn is, which the prefetch already has. "Injected into the
prompt" is the real use.

One row per L3 doc id (= the L3 result's metadata.message_id = the orchestrator's l3_doc_id). ``uses`` is a faded
count: on each use it becomes ``uses * 0.5 ** (elapsed / half_life) + 1``, so the same 24 h half-life as the paths;
``score`` reads it faded to now. Same store file, same connection and lock as the rest of the brain, in a thread.

Not to be confused with memory.usage_probe's ``l3_usage`` log line, a different, approximate signal (term overlap
between the reply and the injected entries). The table is named ``l3_injected`` for that reason. The reference count
to compare against is tool_gate's ``prefetch_quality ... used=`` (packed entries, wave 2 included), so per turn
``docs`` (originals only) <= ``used``.
"""
from __future__ import annotations

import asyncio
import time

__all__ = ["SCHEMA", "record", "scores"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS l3_injected (doc_id TEXT PRIMARY KEY, uses REAL NOT NULL, last_used REAL NOT NULL,
    first_used REAL NOT NULL);
"""


def _fade(dt: float, half_life_hours: float) -> float:
    return 0.5 ** (max(0.0, dt) / (float(half_life_hours) * 3600))


async def record(brain, doc_ids: list[str], now: float | None = None) -> int:
    """One use for each distinct doc id (a doc injected twice in one turn counts once). Returns how many."""
    ids = sorted({str(d) for d in doc_ids or [] if d})
    if not ids:
        return 0
    now = time.time() if now is None else now
    half_life = float(brain.cfg["half_life_hours"])

    def _write() -> int:
        db = brain._db
        db.executescript(SCHEMA)
        old = dict(((r[0], (r[1], r[2])) for r in db.execute(
            f"SELECT doc_id, uses, last_used FROM l3_injected WHERE doc_id IN ({','.join('?' * len(ids))})", ids)))
        rows = []
        for d in ids:
            uses, last = old.get(d, (0.0, now))
            rows.append((d, uses * _fade(now - last, half_life) + 1.0, now, now))
        db.executemany("INSERT INTO l3_injected (doc_id, uses, last_used, first_used) VALUES (?, ?, ?, ?) ON CONFLICT "
                       "(doc_id) DO UPDATE SET uses = excluded.uses, last_used = excluded.last_used", rows)
        db.commit()
        return len(rows)
    async with brain.lock:
        return await asyncio.to_thread(_write)


async def scores(brain, doc_ids: list[str], now: float | None = None) -> dict[str, float]:
    """The faded use count of each doc id that was ever used (absent = never used)."""
    ids = sorted({str(d) for d in doc_ids or [] if d})
    if not ids:
        return {}
    now = time.time() if now is None else now
    half_life = float(brain.cfg["half_life_hours"])

    def _read() -> dict[str, float]:
        brain._db.executescript(SCHEMA)
        rows = brain._db.execute(
            f"SELECT doc_id, uses, last_used FROM l3_injected WHERE doc_id IN ({','.join('?' * len(ids))})", ids)
        return {d: u * _fade(now - last, half_life) for d, u, last in rows}
    async with brain.lock:
        return await asyncio.to_thread(_read)
