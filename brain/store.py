"""brain.store — the memory brain's SQLite store and one sleep cycle (split out of brain/brain.py, 2026-11-15).

Blind write + path links (_ingest), fading by the time elapsed (_fade), the open-items nucleus (_nucleus) and a
cycle in small slices (_cycle). All SQLite access is serialised by ``Brain.lock`` and runs in a thread. The
process-level API (config, the shared instance, the request hook's record_turn, the sleep loop) is brain.brain.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from admin_panel.coord_feed import split_tools  # imported once here: inside the loop it ate the first cycle's budget
from brain.rules import is_open_question, is_promise, lane_of
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["Brain"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, ts REAL NOT NULL, chat_id TEXT NOT NULL, lane TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, l3_id TEXT, source TEXT, ingested_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_chat_ts ON events (chat_id, lane, ts);
CREATE TABLE IF NOT EXISTS paths (src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, weight REAL NOT NULL,
    last_reinforced REAL NOT NULL, faded_at REAL, PRIMARY KEY (src, dst, kind));
CREATE TABLE IF NOT EXISTS nucleus (chat_id TEXT NOT NULL, lane TEXT NOT NULL, open_items TEXT NOT NULL,
    updated_ts REAL NOT NULL, PRIMARY KEY (chat_id, lane));
CREATE TABLE IF NOT EXISTS cursor (chat_id TEXT PRIMARY KEY, last_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cycles (id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, ms REAL NOT NULL,
    events INTEGER NOT NULL, links INTEGER NOT NULL, open_items INTEGER NOT NULL);
"""


def _event_id(chat_id: str, m: dict) -> str:
    key = f"{chat_id}|{m.get('role')}|{float(m.get('timestamp', 0)):.6f}|{str(m.get('content', ''))[:200]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def _clean(m: dict) -> str:
    text = str(m.get("content") or "")
    if m.get("role") == "assistant":  # the stored reply may carry the tool-call evidence block before it
        text = split_tools(text)[1]
    return text.strip()


class Brain:
    """The store plus one sleep cycle. All SQLite access is serialised by ``self.lock`` and runs in a thread."""

    def __init__(self, cfg: dict, clock=time.time) -> None:
        self.cfg, self.lock, self._clock = dict(cfg), asyncio.Lock(), clock
        Path(self.cfg["db_path"]).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.cfg["db_path"], check_same_thread=False, timeout=5.0)  # busy -> error
        self._db.executescript(_SCHEMA)
        if "faded_at" not in {r[1] for r in self._db.execute("PRAGMA table_info(paths)")}:  # a store made before it
            self._db.execute("ALTER TABLE paths ADD COLUMN faded_at REAL")
        half_life_s = float(self.cfg["half_life_hours"]) * 3600
        # weight x 0.5 ** (seconds elapsed / half-life): the same forgetting whether cycles run every 5 s or 5 min
        self._db.create_function("fade", 1, lambda dt: 0.5 ** (max(0.0, dt or 0.0) / half_life_s), deterministic=True)

    def close(self) -> None:
        self._db.close()

    def _ingest(self, chat_id: str, messages: list[dict], budget_ms: float | None,
                lane: str | None = None) -> tuple[int, int, list[str]]:
        """Blind write + path links, for messages newer than the chat's cursor, within the row/time budget.
        ``lane`` (the request hook's real lane) overrides the guess from the text. Returns (events, links, lanes)."""
        db, now, t0 = self._db, self._clock(), time.perf_counter()
        row = db.execute("SELECT last_ts FROM cursor WHERE chat_id = ?", (chat_id,)).fetchone()
        cursor = row[0] if row else 0.0
        fresh = [m for m in messages if float(m.get("timestamp", 0)) > cursor and m.get("role") in ("user", "assistant")]
        events = links = 0
        lanes: set[str] = set()
        user_lane = "human"  # an L2 reply has no header of its own: it belongs to the lane of the message it answers
        for done, m in enumerate(fresh[: int(self.cfg["max_events_per_cycle"])]):
            # the budget is checked only after a first message: a busy process once spent it on the first query
            # and stored nothing, ever (2026-11-15). None = no slicing (the request hook's 2-message turn).
            if done and budget_ms is not None and (time.perf_counter() - t0) * 1000 > budget_ms:
                break
            if m["role"] == "user":
                user_lane = lane_of(m)
            ts, lane_m, eid = float(m["timestamp"]), lane or user_lane, _event_id(chat_id, m)
            # a turn's user message and reply share one timestamp in L2: at equal time the user message comes first
            prev = db.execute("SELECT id, role FROM events WHERE chat_id = ? AND lane = ? AND id != ? AND (ts < ? OR "
                              "(ts = ? AND role = 'user' AND ? = 'assistant')) ORDER BY ts DESC, role = 'user' "
                              "LIMIT 1", (chat_id, lane_m, eid, ts, ts, m["role"])).fetchone()
            inserted = db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, ts, chat_id, lane_m, m["role"], _clean(m), m.get("l3_id"), m.get("source"), now)).rowcount
            if inserted and prev:
                kind = "answered_by" if prev[1] == "user" and m["role"] == "assistant" else "next_turn"
                db.execute("INSERT INTO paths VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (src, dst, kind) DO UPDATE SET "
                           "weight = weight * fade(excluded.faded_at - coalesce(faded_at, last_reinforced)) + "
                           "excluded.weight, last_reinforced = excluded.last_reinforced, faded_at = excluded.faded_at",
                           (prev[0], eid, kind, float(self.cfg["reinforce"]), now, now))
                links += 1
            events += inserted
            lanes.add(lane_m)
            db.execute("INSERT INTO cursor VALUES (?, ?) ON CONFLICT (chat_id) DO UPDATE SET last_ts = excluded.last_ts",
                       (chat_id, ts))
        db.commit()
        return events, links, sorted(lanes)

    def _fade(self, now: float) -> None:
        """Links get weaker by the time elapsed since they were last faded (half-life ``half_life_hours``); a cycle
        run seconds after the previous one changes them by almost nothing. Never deleted: forgetting = fainter."""
        self._db.execute("UPDATE paths SET weight = weight * fade(? - coalesce(faded_at, last_reinforced)), "
                         "faded_at = ? WHERE coalesce(faded_at, last_reinforced) < ?", (now, now, now))
        self._db.commit()

    def _nucleus(self, chat_id: str, lane: str) -> int:
        """Open items by rules (an unanswered question; a reply promising to come back), newest first. Shadow."""
        rows = self._db.execute("SELECT id, ts, role, text FROM events WHERE chat_id = ? AND lane = ? ORDER BY ts DESC "
                                "LIMIT 200", (chat_id, lane)).fetchall()
        items, answered = [], False
        for eid, ts, role, text in rows:  # newest → oldest: a question is open only if nothing answered it after
            if role == "assistant":
                if is_promise(text):
                    items.append({"kind": "promised", "event": eid, "ts": ts, "text": text[:240]})
                answered = True
            elif role == "user" and not answered and is_open_question(text):
                items.append({"kind": "unanswered", "event": eid, "ts": ts, "text": text[:240]})
            if len(items) >= int(self.cfg["nucleus_items"]):
                break
        self._db.execute("INSERT INTO nucleus VALUES (?, ?, ?, ?) ON CONFLICT (chat_id, lane) DO UPDATE SET "
                         "open_items = excluded.open_items, updated_ts = excluded.updated_ts",
                         (chat_id, lane, json.dumps(items, ensure_ascii=False), self._clock()))
        self._db.commit()
        return len(items)

    def _cycle(self, batches: list[tuple[str, list[dict]]]) -> dict:
        started, t0 = self._clock(), time.perf_counter()
        events = links = items = 0
        for chat_id, messages in batches:
            left = float(self.cfg["max_cycle_ms"]) - (time.perf_counter() - t0) * 1000
            if left <= 0:
                break
            e, k, _ = self._ingest(chat_id, messages, left)
            events, links = events + e, links + k
        # the nucleus of every lane with events newer than its last update, whoever wrote them (the request hook
        # writes the turns before the sleep sees them in L2, so "lanes this cycle ingested" would be empty)
        for chat_id, lane in self._db.execute(
                "SELECT DISTINCT chat_id, lane FROM events e WHERE ingested_at > coalesce((SELECT updated_ts FROM "
                "nucleus n WHERE n.chat_id = e.chat_id AND n.lane = e.lane), 0)").fetchall():
            items += self._nucleus(chat_id, lane)
        self._fade(started)
        ms = (time.perf_counter() - t0) * 1000
        self._db.execute("INSERT INTO cycles (started, ms, events, links, open_items) VALUES (?, ?, ?, ?, ?)",
                         (started, ms, events, links, items))
        self._db.commit()
        return {"ms": round(ms, 1), "events": events, "links": links, "open_items": items}

    async def sleep_once(self, working, chat_ids: list[str]) -> dict:
        """One sleep cycle: read the chats' L2 messages (read-only), then process them under the store lock."""
        batches = [(c, await working.get_messages(c)) for c in chat_ids]
        async with self.lock:
            result = await asyncio.to_thread(self._cycle, batches)
        log.info("brain: cycle ms=%.1f events=%d links=%d open_items=%d", result["ms"], result["events"],
                 result["links"], result["open_items"])
        return result

    async def run_forever(self, working, chat_ids: list[str], stop: asyncio.Event) -> None:
        """Cycles until ``stop`` is set. The stop is only seen BETWEEN cycles, so a cycle is never cut mid-write
        (cancelling an awaited to_thread would release the lock while its SQLite thread keeps writing)."""
        while not stop.is_set():  # one failed cycle is logged; the loop (and the bot) carry on
            try:
                await self.sleep_once(working, chat_ids)
            except Exception as exc:  # noqa: BLE001
                log.warning("brain: cycle failed: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), float(self.cfg["interval_seconds"]))
            except asyncio.TimeoutError:
                pass

    async def nucleus(self, chat_id: str, lane: str = "human") -> list[dict]:
        async with self.lock:
            row = await asyncio.to_thread(lambda: self._db.execute(
                "SELECT open_items FROM nucleus WHERE chat_id = ? AND lane = ?", (chat_id, lane)).fetchone())
        return json.loads(row[0]) if row else []
