"""brain.brain — memory brain v0: blind write, paths that strengthen and fade, an "open items" nucleus (Laris + GOAT,
2026-11-15). A new skeleton BESIDE the existing L1/L2/L3 memory: it reads the conversation from L2 (Redis) and never
writes to Redis, Chroma or Letta. Its own store is one SQLite file on persistent disk. Off by default ([brain] enabled).

- Blind write: every new L2 message becomes an ``events`` row, unfiltered, no judgement, no embedding.
- Sleep: a background asyncio task, every ``interval_seconds``, in small slices (at most ``max_events_per_cycle``
  rows or ``max_cycle_ms``): ingest the new messages, link the path (user → answered_by → assistant → next_turn →
  user), reinforce the links just walked, fade every other link a little. Forgetting = a weaker path, never a delete.
- Nucleus (shadow): per chat and lane, the questions still unanswered and what GOAT said it would do ("revin",
  "o să verific"...), found by cheap rules. Computed and stored, NOT injected into any prompt in v0.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import tomllib
from pathlib import Path

from admin_panel.coord_feed import split_tools  # imported once here: inside the loop it ate the first cycle's budget
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["CONFIG", "Brain", "load_config", "lane_of", "is_open_question", "is_promise", "start", "is_enabled",
           "record_turn"]

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULTS = {"enabled": False, "db_path": "", "interval_seconds": 60, "max_events_per_cycle": 200,
             "max_cycle_ms": 200, "reinforce": 1.0, "decay": 0.98, "nucleus_items": 10, "record_budget_ms": 20}
_DIGEST_PREFIX = "Mesaje noi pe canalul agent-coord"  # memory.temporal_route.CHANNEL_DIGEST_PREFIX (not imported)
_PROMISE = re.compile(r"\b(revin|o s[ăa] (verific|fac|caut|revin|v[ăa]d|te anun[țt])|voi (verifica|reveni|face|"
                      r"c[ăa]uta)|te anun[țt]|urmeaz[ăa] s[ăa]|fac (asta )?mai t[âa]rziu|m[ăa] uit (și|si) revin)\b", re.I)
_QUESTION_START = re.compile(r"^(ce|cum|c[âa]nd|unde|de ce|cine|care|c[âa]t|c[âa]te|po[țt]i|e[șs]ti|avem)\b", re.I)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, ts REAL NOT NULL, chat_id TEXT NOT NULL, lane TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, l3_id TEXT, source TEXT, ingested_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_chat_ts ON events (chat_id, lane, ts);
CREATE TABLE IF NOT EXISTS paths (src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, weight REAL NOT NULL,
    last_reinforced REAL NOT NULL, PRIMARY KEY (src, dst, kind));
CREATE TABLE IF NOT EXISTS nucleus (chat_id TEXT NOT NULL, lane TEXT NOT NULL, open_items TEXT NOT NULL,
    updated_ts REAL NOT NULL, PRIMARY KEY (chat_id, lane));
CREATE TABLE IF NOT EXISTS cursor (chat_id TEXT PRIMARY KEY, last_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cycles (id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, ms REAL NOT NULL,
    events INTEGER NOT NULL, links INTEGER NOT NULL, open_items INTEGER NOT NULL);
"""


def load_config(path: Path | None = None) -> dict:
    """``config/brain.toml`` [brain] over the defaults; a missing file means the defaults (off)."""
    cfg = dict(_DEFAULTS)
    path = path or _ROOT / "config" / "brain.toml"
    if path.exists():
        with open(path, "rb") as f:
            cfg.update(tomllib.load(f).get("brain", {}))
    if not cfg["db_path"]:
        state = (os.environ.get("XDG_STATE_HOME") or "").strip()
        cfg["db_path"] = str((Path(state) if state else Path.home() / ".local" / "state") / "goat2" / "brain.sqlite")
    return cfg


CONFIG = load_config()


def lane_of(message: dict) -> str:
    """Which population a stored L2 message belongs to (human / channel / dag), from what it says about itself."""
    text = str(message.get("content") or "").lstrip()
    if message.get("role") == "user" and text.startswith(_DIGEST_PREFIX):
        return "channel"
    if message.get("role") == "user" and text.startswith("[Workflow '"):
        return "dag"
    return "human"


def is_open_question(text: str) -> bool:
    """A short message that asks something: ends with '?' or starts with a Romanian question word."""
    t = (text or "").strip()
    return 0 < len(t) < 300 and (t.endswith("?") or bool(_QUESTION_START.match(t)))


def is_promise(text: str) -> bool:
    return bool(_PROMISE.search(text or ""))


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

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = dict(cfg or CONFIG)
        self.lock = asyncio.Lock()
        Path(self.cfg["db_path"]).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.cfg["db_path"], check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ── blind write + paths (sync, called under the lock in a thread) ──────────────────────────────────────────────
    def _ingest(self, chat_id: str, messages: list[dict], budget_ms: float,
                lane: str | None = None) -> tuple[int, int, list[str]]:
        """New messages only (timestamp after this chat's cursor), oldest first, until the row or time budget runs
        out; links each to the previous event of the same lane. ``lane`` (the turn's real lane, from the request
        hook) overrides the guess from the text. Returns (events, links, touched lanes)."""
        db, now, t0 = self._db, time.time(), time.perf_counter()
        row = db.execute("SELECT last_ts FROM cursor WHERE chat_id = ?", (chat_id,)).fetchone()
        cursor = row[0] if row else 0.0
        fresh = [m for m in messages if float(m.get("timestamp", 0)) > cursor and m.get("role") in ("user", "assistant")]
        events = links = 0
        lanes: set[str] = set()
        for m in fresh[: int(self.cfg["max_events_per_cycle"])]:
            if (time.perf_counter() - t0) * 1000 > budget_ms:
                break
            ts, lane_m, eid = float(m["timestamp"]), lane or lane_of(m), _event_id(chat_id, m)
            # a turn's user message and reply share one timestamp in L2: at equal time the user message comes first
            prev = db.execute("SELECT id, role FROM events WHERE chat_id = ? AND lane = ? AND id != ? AND (ts < ? OR "
                              "(ts = ? AND role = 'user' AND ? = 'assistant')) ORDER BY ts DESC, role = 'user' "
                              "LIMIT 1", (chat_id, lane_m, eid, ts, ts, m["role"])).fetchone()
            inserted = db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, ts, chat_id, lane_m, m["role"], _clean(m), m.get("l3_id"), m.get("source"), now)).rowcount
            if inserted and prev:
                kind = "answered_by" if prev[1] == "user" and m["role"] == "assistant" else "next_turn"
                db.execute("INSERT INTO paths VALUES (?, ?, ?, ?, ?) ON CONFLICT (src, dst, kind) DO UPDATE SET "
                           "weight = weight + excluded.weight, last_reinforced = excluded.last_reinforced",
                           (prev[0], eid, kind, float(self.cfg["reinforce"]), now))
                links += 1
            events += inserted
            lanes.add(lane_m)
            db.execute("INSERT INTO cursor VALUES (?, ?) ON CONFLICT (chat_id) DO UPDATE SET last_ts = excluded.last_ts",
                       (chat_id, ts))
        db.commit()
        return events, links, sorted(lanes)

    def _fade(self, now: float) -> None:
        """Every link not walked in this cycle gets a little weaker. Never deleted: forgetting = a fainter path."""
        self._db.execute("UPDATE paths SET weight = weight * ? WHERE last_reinforced < ?", (float(self.cfg["decay"]), now))
        self._db.commit()

    def _nucleus(self, chat_id: str, lane: str) -> int:
        """Open items, by rules: a question of Laris's with no reply after it; a reply where GOAT said it would come
        back or do something. Newest first, at most ``nucleus_items``. Stored only (shadow)."""
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
                         (chat_id, lane, json.dumps(items, ensure_ascii=False), time.time()))
        self._db.commit()
        return len(items)

    def _cycle(self, batches: list[tuple[str, list[dict]]]) -> dict:
        started, t0 = time.time(), time.perf_counter()
        events = links = items = 0
        for chat_id, messages in batches:
            left = float(self.cfg["max_cycle_ms"]) - (time.perf_counter() - t0) * 1000
            if left <= 0:
                break
            e, k, lanes = self._ingest(chat_id, messages, left)
            events, links = events + e, links + k
            for lane in lanes:
                items += self._nucleus(chat_id, lane)
        self._fade(started)
        ms = (time.perf_counter() - t0) * 1000
        self._db.execute("INSERT INTO cycles (started, ms, events, links, open_items) VALUES (?, ?, ?, ?, ?)",
                         (started, ms, events, links, items))
        self._db.commit()
        return {"ms": round(ms, 1), "events": events, "links": links, "open_items": items}

    # ── async surface ──────────────────────────────────────────────────────────────────────────────────────────
    async def sleep_once(self, working, chat_ids: list[str]) -> dict:
        """One sleep cycle: read the chats' L2 messages (read-only), then process them under the store lock."""
        batches = [(c, await working.get_messages(c)) for c in chat_ids]
        async with self.lock:
            result = await asyncio.to_thread(self._cycle, batches)
        log.info("brain: cycle ms=%.1f events=%d links=%d open_items=%d", result["ms"], result["events"],
                 result["links"], result["open_items"])
        return result

    async def run_forever(self, working, chat_ids: list[str]) -> None:
        while True:
            try:
                await self.sleep_once(working, chat_ids)
            except Exception as exc:  # noqa: BLE001 - the brain must never take the bot down
                log.warning("brain: cycle failed: %s", exc)
            await asyncio.sleep(float(self.cfg["interval_seconds"]))

    async def nucleus(self, chat_id: str, lane: str = "human") -> list[dict]:
        async with self.lock:
            row = await asyncio.to_thread(lambda: self._db.execute(
                "SELECT open_items FROM nucleus WHERE chat_id = ? AND lane = ?", (chat_id, lane)).fetchone())
        return json.loads(row[0]) if row else []


_instance: Brain | None = None


def is_enabled() -> bool:
    """The one gate the orchestrator checks before touching the brain: [brain] enabled (false by default)."""
    return bool(CONFIG.get("enabled"))


async def record_turn(chat_id: str, messages: list[dict], lane: str = "human") -> int:
    """Blind write from the request loop: the turn's exact L2 messages, into the store, right away (same ids as
    the sleep cycle's L2 read, so they are never stored twice). Bounded by ``record_budget_ms``. The caller runs it
    as a background task and swallows any error: it can never cost the reply. Returns the events written."""
    global _instance
    if not is_enabled():
        return 0
    if _instance is None:
        _instance = Brain(CONFIG)
    brain = _instance
    async with brain.lock:
        events, _, _ = await asyncio.to_thread(brain._ingest, chat_id, messages,
                                               float(CONFIG["record_budget_ms"]), lane)
    return events


def start(working, chat_ids: list[str], cfg: dict | None = None) -> asyncio.Task | None:
    """The sleep loop as a background task, or None when [brain] is off (the default). Not wired into the bot in v0."""
    cfg = cfg or CONFIG
    if not cfg.get("enabled"):
        return None
    brain = Brain(cfg)
    return asyncio.create_task(brain.run_forever(working, [c for c in chat_ids if c]))
