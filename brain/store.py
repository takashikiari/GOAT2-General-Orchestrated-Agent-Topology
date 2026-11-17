"""brain.store — the memory brain's SQLite store and one sleep cycle (split out of brain/brain.py, 2026-11-15).

Blind write (_ingest), the open-items nucleus (_nucleus) and a cycle in small slices (_cycle). The path links and
their fading were removed on 2026-11-17 (Laris): in three days no link was ever reinforced and nothing read them.
Stores made before keep their old ``paths`` table untouched; nothing writes or reads it any more. All SQLite access
is serialised by ``Brain.lock`` and runs in a thread. The process-level API (config, the shared instance, the request hook's record_turn, the sleep loop) is brain.brain.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from admin_panel.coord_feed import split_tools  # imported once here: inside the loop it ate the first cycle's budget
from brain.rules import _PROMISE, _QUOTED, _SENTENCES, _WHEN, is_open_question, lane_of
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["Brain"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, ts REAL NOT NULL, chat_id TEXT NOT NULL, lane TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, l3_id TEXT, source TEXT, ingested_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_chat_ts ON events (chat_id, lane, ts);
CREATE TABLE IF NOT EXISTS nucleus (chat_id TEXT NOT NULL, lane TEXT NOT NULL, open_items TEXT NOT NULL,
    updated_ts REAL NOT NULL, PRIMARY KEY (chat_id, lane));
CREATE TABLE IF NOT EXISTS cursor (chat_id TEXT PRIMARY KEY, last_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cycles (id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, ms REAL NOT NULL,
    events INTEGER NOT NULL, links INTEGER NOT NULL, open_items INTEGER NOT NULL, hook_events INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS hook_pending (id INTEGER PRIMARY KEY CHECK (id = 1), events INTEGER NOT NULL);
"""


# "nu trimit nimic până la 15:30", "n-o să verific azi": a negation in the two words before the verb is no promise
_NEGATED = re.compile(r"(?:^|[\s,;:(—-])(?:nu|nici|n-)(?:\s+\S+){0,2}\s*$", re.I)


def promise_sentence(text: str) -> str | None:
    """The sentence of ``text`` that carries a promise (committed future verb + time term, outside quotes, not
    negated), or None. The nucleus stores THIS, not the reply's opening (2026-11-15: three live items showed only
    "🐐 Da, partea de cod e livrată…", nothing about what was promised)."""
    for sentence in _SENTENCES.split(_QUOTED.sub(" ", text or "")):
        verb = _PROMISE.search(sentence)
        if verb and _WHEN.search(sentence) and not _NEGATED.search(sentence[:verb.start()]):
            return sentence.strip()
    return None


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
        if "hook_events" not in {r[1] for r in self._db.execute("PRAGMA table_info(cycles)")}:  # same: an older store
            self._db.execute("ALTER TABLE cycles ADD COLUMN hook_events INTEGER NOT NULL DEFAULT 0")
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _ingest(self, chat_id: str, messages: list[dict], budget_ms: float | None,
                lane: str | None = None) -> tuple[int, list[str]]:
        """Blind write, for messages newer than the chat's cursor, within the row/time budget. ``lane`` (the
        request hook's real lane) overrides the guess from the text. Returns (events, lanes)."""
        db, now, t0 = self._db, self._clock(), time.perf_counter()
        row = db.execute("SELECT last_ts FROM cursor WHERE chat_id = ?", (chat_id,)).fetchone()
        cursor = row[0] if row else 0.0
        fresh = [m for m in messages if float(m.get("timestamp", 0)) > cursor and m.get("role") in ("user", "assistant")]
        events = 0
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
            inserted = db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, ts, chat_id, lane_m, m["role"], _clean(m), m.get("l3_id"), m.get("source"), now)).rowcount
            events += inserted
            lanes.add(lane_m)
            db.execute("INSERT INTO cursor VALUES (?, ?) ON CONFLICT (chat_id) DO UPDATE SET last_ts = excluded.last_ts",
                       (chat_id, ts))
        db.commit()
        return events, sorted(lanes)

    def _nucleus(self, chat_id: str, lane: str) -> int:
        """Open items by rules (an unanswered question; a reply promising to come back), newest first. Only the
        newest promise is "promised"; older ones are "promised_old" (a later reply came after them, so they are most
        likely kept or stale) and are not shown in the prompt block (brain/inject.py)."""
        rows = self._db.execute("SELECT id, ts, role, text FROM events WHERE chat_id = ? AND lane = ? ORDER BY ts DESC "
                                "LIMIT 200", (chat_id, lane)).fetchall()
        items, answered, promised = [], False, False
        for eid, ts, role, text in rows:  # newest → oldest: a question is open only if nothing answered it after
            if role == "assistant":
                sentence = promise_sentence(text)
                if sentence:
                    items.append({"kind": "promised_old" if promised else "promised", "event": eid, "ts": ts,
                                  "text": sentence[:240]})
                    promised = True
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

    def _hook_write(self, chat_id: str, messages: list[dict], lane: str) -> tuple[int, int]:
        """The request hook's write (brain.record_turn): ingest the turn, refresh the nucleus of every lane it wrote
        right away (the prompt block must not lag a sleep cycle behind the turn), and add the events to the pending
        hook counter that the next cycle reports as ``hook_events``: a table, so the count survives a restart.
        Returns (events, nucleus items of the refreshed lanes)."""
        events, lanes = self._ingest(chat_id, messages, None, lane)
        items = sum(self._nucleus(chat_id, written) for written in lanes)
        if events:
            self._db.execute("INSERT INTO hook_pending (id, events) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET "
                             "events = events + excluded.events", (events,))
            self._db.commit()
        return events, items

    def _cycle(self, batches: list[tuple[str, list[dict]]]) -> dict:
        started, t0 = self._clock(), time.perf_counter()
        events = items = 0
        for chat_id, messages in batches:
            left = float(self.cfg["max_cycle_ms"]) - (time.perf_counter() - t0) * 1000
            if left <= 0:
                break
            e, _ = self._ingest(chat_id, messages, left)
            events += e
        # the nucleus of every lane with events newer than its last update, whoever wrote them (the request hook
        # writes the turns before the sleep sees them in L2, so "lanes this cycle ingested" would be empty)
        for chat_id, lane in self._db.execute(
                "SELECT DISTINCT chat_id, lane FROM events e WHERE ingested_at > coalesce((SELECT updated_ts FROM "
                "nucleus n WHERE n.chat_id = e.chat_id AND n.lane = e.lane), 0)").fetchall():
            items += self._nucleus(chat_id, lane)
        # events = what THIS cycle ingested from L2 (the baseline's meaning, unchanged); hook_events = what the request
        # hook wrote since the previous cycle (its turns are already in the store, so the cycle's own count is 0)
        row = self._db.execute("SELECT events FROM hook_pending WHERE id = 1").fetchone()
        hook_events = int(row[0]) if row else 0
        ms = (time.perf_counter() - t0) * 1000
        self._db.execute("INSERT INTO cycles (started, ms, events, links, open_items, hook_events) VALUES "
                         "(?, ?, ?, ?, ?, ?)", (started, ms, events, 0, items, hook_events))  # links: kept 0
        self._db.execute("UPDATE hook_pending SET events = 0 WHERE id = 1")
        self._db.commit()
        # open_items = items of the lanes refreshed in THIS cycle; nucleus_total = what the whole nucleus holds now
        total = sum(len(json.loads(r[0])) for r in self._db.execute("SELECT open_items FROM nucleus"))
        return {"ms": round(ms, 1), "events": events, "open_items": items, "nucleus_total": total,
                "hook_events": hook_events}

    async def sleep_once(self, working, chat_ids: list[str]) -> dict:
        """One sleep cycle: read the chats' L2 messages (read-only), then process them under the store lock."""
        batches = [(c, await working.get_messages(c)) for c in chat_ids]
        async with self.lock:
            result = await asyncio.to_thread(self._cycle, batches)
        log.info("brain: cycle ms=%.1f events=%d hook_events=%d open_items=%d nucleus_total=%d", result["ms"],
                 result["events"], result["hook_events"], result["open_items"], result["nucleus_total"])
        return result

    async def run_forever(self, working, chat_ids: list[str], stop: asyncio.Event, on_tick=None) -> None:
        """Cycles until ``stop`` is set. The stop is only seen BETWEEN cycles, so a cycle is never cut mid-write
        (cancelling an awaited to_thread would release the lock while its SQLite thread keeps writing).
        ``on_tick`` (an async callable, e.g. the scheduled messages of brain/scheduler.py) runs after each cycle,
        on the same tick; its failure is logged and never stops the loop."""
        while not stop.is_set():  # one failed cycle is logged; the loop (and the bot) carry on
            try:
                await self.sleep_once(working, chat_ids)
            except Exception as exc:  # noqa: BLE001
                log.warning("brain: cycle failed: %s", exc)
            if on_tick is not None:
                try:
                    await on_tick()
                except Exception as exc:  # noqa: BLE001
                    log.warning("brain: tick hook failed: %s: %s", type(exc).__name__, exc)
            try:
                await asyncio.wait_for(stop.wait(), float(self.cfg["interval_seconds"]))
            except asyncio.TimeoutError:
                pass

    async def nucleus(self, chat_id: str, lane: str = "human") -> list[dict]:
        async with self.lock:
            row = await asyncio.to_thread(lambda: self._db.execute(
                "SELECT open_items FROM nucleus WHERE chat_id = ? AND lane = ?", (chat_id, lane)).fetchone())
        return json.loads(row[0]) if row else []
