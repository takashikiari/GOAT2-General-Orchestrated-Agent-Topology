"""brain.brain — memory brain v0: blind write, paths that strengthen and fade, an "open items" nucleus (Laris + GOAT,
2026-11-15). A new skeleton BESIDE the existing L1/L2/L3 memory: it reads the conversation from L2 (Redis) and never
writes to Redis, Chroma or Letta. Its own store is one SQLite file on persistent disk. Off by default ([brain] enabled).

- Blind write: every new L2 message becomes an ``events`` row, unfiltered, no judgement, no embedding.
- Sleep: a background asyncio task, every ``interval_seconds``, in small slices (``max_events_per_cycle`` rows or
  ``max_cycle_ms``): ingest, link the path (user → answered_by → assistant → next_turn → user), reinforce the links
  walked, and fade the rest by the TIME elapsed (half-life ``half_life_hours``), not per cycle. Never a delete.
- Nucleus (shadow): per chat and lane, the questions still unanswered and what GOAT said it would do ("revin",
  "o să verific"...), found by cheap rules. Computed and stored; with [brain] inject it is read back as a
  short block in GOAT's system prompt (``nucleus`` + brain/inject.py, 2026-11-15).
"""
from __future__ import annotations

import asyncio
import os
import tomllib
from pathlib import Path

from brain.rules import is_open_question, is_promise, lane_of  # noqa: F401 - re-exported, the cheap text rules
from brain.store import Brain  # noqa: F401 - re-exported: the store and one sleep cycle
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["CONFIG", "Brain", "load_config", "lane_of", "is_open_question", "is_promise", "start", "is_enabled",
           "record_turn", "get_instance", "nucleus"]

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULTS = {"enabled": False, "db_path": "", "interval_seconds": 60, "max_events_per_cycle": 200,
             "max_cycle_ms": 200, "reinforce": 1.0, "half_life_hours": 24.0, "nucleus_items": 10,
             "record_slow_ms": 2000, "inject": False, "inject_max_chars": 600}

def load_config(path: Path | None = None) -> dict:
    """``config/brain.toml`` [brain] over the defaults; a missing file means the defaults (off)."""
    cfg = dict(_DEFAULTS)
    path = path or _ROOT / "config" / "brain.toml"
    if path.exists():
        with open(path, "rb") as f:
            cfg.update(tomllib.load(f).get("brain", {}))
    # env overrides (the test suite uses them so pytest never writes into the real store, like GOAT_LOG_DIR)
    if os.environ.get("GOAT_BRAIN_ENABLED", "").strip():
        cfg["enabled"] = os.environ["GOAT_BRAIN_ENABLED"].strip().lower() in ("1", "true", "yes", "on")
    if os.environ.get("GOAT_BRAIN_DB", "").strip():
        cfg["db_path"] = os.environ["GOAT_BRAIN_DB"].strip()
    if not cfg["db_path"]:
        state = (os.environ.get("XDG_STATE_HOME") or "").strip()
        cfg["db_path"] = str((Path(state) if state else Path.home() / ".local" / "state") / "goat2" / "brain.sqlite")
    return cfg


CONFIG = load_config()


_instance: Brain | None = None


def get_instance(cfg: dict | None = None) -> Brain:
    """The ONE Brain of the process: the request hook and the sleep share its connection and lock."""
    global _instance
    if _instance is None:
        _instance = Brain(cfg or CONFIG)
    return _instance


def is_enabled() -> bool:
    """The one gate the orchestrator checks before touching the brain: [brain] enabled (false by default)."""
    return bool(CONFIG.get("enabled"))


async def record_turn(chat_id: str, messages: list[dict], lane: str = "human") -> int:
    """Blind write from the request loop: the turn's exact L2 messages (all of them: it is one turn), into the
    store, right away (same ids as the sleep cycle's L2 read, so never stored twice). The caller runs it as a
    background task, never cancels it and swallows any error: it can never cost the reply."""
    if not is_enabled():
        return 0
    brain = get_instance()
    async with brain.lock:
        events, _, _ = await asyncio.to_thread(brain._ingest, chat_id, messages, None, lane)
    return events


async def nucleus(chat_id: str, lane: str = "human") -> list[dict]:
    """The open items of ``chat_id``/``lane`` as the last sleep cycle stored them, or [] when [brain] is off. The gate
    comes first, so a disabled brain never creates a store; the read runs on the shared Brain, under its lock, in a
    thread (Brain.nucleus)."""
    if not is_enabled():
        return []
    return await get_instance().nucleus(chat_id, lane)


_sleep: tuple[asyncio.Task, asyncio.Event] | None = None


def start(working, chat_ids: list[str], cfg: dict | None = None) -> asyncio.Task | None:
    """The ONE sleep loop of the process, on the shared Brain, or None when [brain] is off. Idempotent: while a
    loop is running, a second call returns the same task instead of starting another."""
    global _sleep
    cfg = cfg or CONFIG
    if not cfg.get("enabled"):
        return None
    if _sleep is not None and not _sleep[0].done():
        return _sleep[0]
    stop = asyncio.Event()
    task = asyncio.create_task(get_instance(cfg).run_forever(working, [c for c in chat_ids if c], stop))
    _sleep = (task, stop)
    return task


async def stop_sleep(timeout: float = 10.0) -> None:
    """Clean shutdown: ask the loop to stop, let the cycle in progress finish, then await the task (cancelled
    only if it does not end within ``timeout``, i.e. while it waits between cycles or is stuck)."""
    global _sleep
    if _sleep is None:
        return
    task, stop = _sleep
    _sleep = None
    stop.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    except Exception:  # noqa: BLE001
        pass
