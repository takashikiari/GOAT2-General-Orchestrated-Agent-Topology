"""tools.schedule_tools — GOAT's entrance to the brain's scheduled messages (Laris + GOAT, 2026-11-15).

Three tools over brain/scheduler.py: ``schedule_message(when, text)``, ``list_scheduled()``,
``cancel_scheduled(task_id)``. The chat is the turn's own (``chat_id`` is injected by the orchestrator, never by the
model). The message goes out on the brain's tick (±1 min), through notify(), as GOAT wrote it.

``when`` is strict, no natural language: ``HH:MM`` (Europe/Bucharest; today if that time is still ahead, else
tomorrow) or ``+30m`` / ``+2h`` (from now). Anything else is refused with a clear error, never guessed.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from brain import brain as _brain
from brain import scheduler as _scheduler
from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["TZ", "parse_when", "build_schedule_tools"]

TZ = ZoneInfo("Europe/Bucharest")
_ABSOLUTE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_RELATIVE = re.compile(r"^\+(\d{1,4})(m|h)$")
_MAX_AHEAD_S = 7 * 24 * 3600
_FORMAT_ERROR = "❌ Format 'when' invalid: {when!r}. Acceptat: 'HH:MM' (ora României) sau '+30m' / '+2h'."


def parse_when(when: str, now: float | None = None) -> float:
    """The unix time ``when`` names, or ValueError with the accepted formats. ``now`` is for tests."""
    w = str(when or "").strip().lower()
    now = time.time() if now is None else now
    rel = _RELATIVE.match(w)
    if rel:
        seconds = int(rel.group(1)) * (60 if rel.group(2) == "m" else 3600)
        if not 0 < seconds <= _MAX_AHEAD_S:
            raise ValueError("❌ 'when' relativ trebuie să fie între +1m și 7 zile.")
        return now + seconds
    absolute = _ABSOLUTE.match(w)
    if absolute:
        local_now = datetime.fromtimestamp(now, TZ)
        at = local_now.replace(hour=int(absolute.group(1)), minute=int(absolute.group(2)), second=0, microsecond=0)
        if at.timestamp() <= now:  # the time has passed today → tomorrow, same wall-clock time
            at = (at.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=TZ)
        return at.timestamp()
    raise ValueError(_FORMAT_ERROR.format(when=when))


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m %H:%M")


def build_schedule_tools() -> list[ToolDefinition]:
    """The three tools, on the process's shared Brain (brain.get_instance)."""

    async def schedule_message(when: str, text: str, chat_id: str = "") -> str:
        try:
            run_at = parse_when(when)
            task_id = await _scheduler.schedule(_brain.get_instance(), chat_id, run_at, text)
        except ValueError as exc:
            return str(exc) if str(exc).startswith("❌") else f"❌ {exc}"
        log.info("schedule_message chat=%s id=%d run_at=%s chars=%d", chat_id, task_id, _local(run_at), len(text))
        return f"✅ Programat #{task_id} pentru {_local(run_at)} (ora României, ±1 min)."

    async def list_scheduled(chat_id: str = "") -> str:
        tasks = await _scheduler.pending(_brain.get_instance(), chat_id)
        if not tasks:
            return "Niciun mesaj programat."
        return "\n".join(f"#{t['id']} {_local(t['run_at'])}: {t['text'][:120]}" for t in tasks)

    async def cancel_scheduled(task_id: int, chat_id: str = "") -> str:
        try:
            ok = await _scheduler.cancel(_brain.get_instance(), chat_id, int(task_id))
        except (TypeError, ValueError):
            return f"❌ task_id invalid: {task_id!r}"
        log.info("cancel_scheduled chat=%s id=%s ok=%s", chat_id, task_id, ok)
        return f"✅ Anulat #{task_id}." if ok else f"❌ #{task_id} nu e un mesaj programat în așteptare în acest chat."

    return [
        ToolDefinition(
            name="schedule_message",
            description=(
                "Schedule a message that YOU send to this chat later, by yourself, even if the user writes nothing. "
                "Use it when you promise to come back at a time (\"te anunț la 18:00\"): schedule the actual message "
                "then. 'when' is strict: 'HH:MM' (Romania time; today if still ahead, else tomorrow) or '+30m' / '+2h'. "
                "Any other format is refused: convert it yourself. Sent within about one minute of the time."),
            parameters={"type": "object", "properties": {
                "when": {"type": "string", "description": "'HH:MM' (Europe/Bucharest) or '+<N>m' / '+<N>h'"},
                "text": {"type": "string", "description": "The exact message to send, as the user should read it"},
            }, "required": ["when", "text"]},
            handler=schedule_message),
        ToolDefinition(
            name="list_scheduled",
            description="List the messages scheduled for this chat that have not gone out yet, soonest first.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=list_scheduled),
        ToolDefinition(
            name="cancel_scheduled",
            description="Cancel a scheduled message of this chat that has not gone out yet, by its # id.",
            parameters={"type": "object", "properties": {
                "task_id": {"type": "integer", "description": "The id shown by schedule_message / list_scheduled"},
            }, "required": ["task_id"]},
            handler=cancel_scheduled),
    ]
