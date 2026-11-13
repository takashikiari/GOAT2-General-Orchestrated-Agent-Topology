"""orchestrator.chat_service — the admin panel chat: GOAT's one conversation, driven from the browser.

It is NOT a second GOAT. A panel message goes through the same ``Orchestrator.run`` and the SAME per-chat lock
the Telegram bot uses (the ``locks`` mapping is the bot's own ``_chat_locks``), for the admin chat id, so Telegram
and the panel are one conversation and two turns never overlap. Each submitted turn runs as a background task and
records numbered events (queued, started, tool_start, tool_end, reply, error, cancelled) that the page polls, which
is what makes it interactive: tool calls show up while they happen, a queued message waits visibly behind a
Telegram turn, and a turn can be stopped.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Awaitable, Callable, MutableMapping

from orchestrator import turn_events
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["ChatService", "ChatBusy", "ChatUnavailable", "Turn"]

_PREVIEW_CHARS = 300
_ERROR_TEXT = "N-am reușit să termin tura asta — poți reîncerca?"
_EMPTY_TEXT = "N-am generat un răspuns pentru mesajul tău — poți reîncerca?"
_ACTIVE = ("queued", "running")


class ChatBusy(Exception):
    """Too many panel turns are already waiting."""


class ChatUnavailable(Exception):
    """No admin chat is configured, so there is no conversation to talk in."""


def _cut(text: str, limit: int = _PREVIEW_CHARS) -> str:
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= limit else text[:limit] + "…"


class Turn:
    def __init__(self, text: str, run_kwargs: dict | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.text = text
        self.run_kwargs = run_kwargs or {}  # e.g. a voice message: source / said_at / voice
        self.status = "queued"
        self.reply: str | None = None
        self.created = time.time()
        self.events: list[dict] = []
        self.task: asyncio.Task | None = None

    def add(self, type_: str, **fields) -> None:
        self.events.append({"seq": len(self.events) + 1, "ts": time.time(), "type": type_, **fields})


class ChatService:
    def __init__(
        self, *, run: Callable[..., Awaitable[str]], locks: MutableMapping[str, asyncio.Lock],
        chat_id: Callable[[], str | None], log_usage: Callable[[str, object, str], object] | None = None,
        mirror: Callable[[str, str, str], Awaitable[None]] | None = None,
        max_turns: int = 20, max_queue: int = 3, max_text: int = 8000,
    ) -> None:
        self._run, self._locks, self._chat_id, self._log_usage = run, locks, chat_id, log_usage
        # mirror(chat_id, role, text): copies a panel turn to Telegram so both show the same conversation
        self._mirror = mirror
        self._max_turns, self._max_queue, self._max_text = max_turns, max_queue, max_text
        self._turns: dict[str, Turn] = {}

    # ── public API (called from the FastAPI routes) ───────────────────────────

    def submit(self, text: str, *, source: str | None = None, said_at: float | None = None,
               voice: bool = False) -> Turn:
        """Start a GOAT turn on ``text``. A voice message passes ``source="voice_live"``, ``said_at`` (when Laris
        spoke) and ``voice=True`` (reply written to be read aloud); a typed message passes none of them."""
        text = (text or "").strip()
        if not text:
            raise ValueError("empty message")
        if len(text) > self._max_text:
            raise ValueError(f"message longer than {self._max_text} characters")
        chat_id = self._chat_id()
        if not chat_id:
            raise ChatUnavailable("no admin chat configured")
        if sum(t.status in _ACTIVE for t in self._turns.values()) >= self._max_queue:
            raise ChatBusy("too many messages waiting")
        run_kwargs = {k: v for k, v in (("source", source), ("said_at", said_at), ("voice", voice)) if v}
        turn = Turn(text, run_kwargs)
        self._turns[turn.id] = turn
        self._prune()
        turn.task = asyncio.create_task(self._run_turn(turn, chat_id))
        return turn

    def get(self, turn_id: str, after: int = 0) -> dict | None:
        turn = self._turns.get(turn_id)
        if turn is None:
            return None
        return {"id": turn.id, "text": turn.text, "status": turn.status, "reply": turn.reply,
                "events": [e for e in turn.events if e["seq"] > after]}

    def cancel(self, turn_id: str) -> bool:
        turn = self._turns.get(turn_id)
        if turn is None or turn.status not in _ACTIVE or turn.task is None:
            return False
        turn.task.cancel()
        return True

    def state(self) -> dict:
        chat_id = self._chat_id()
        locked = bool(chat_id) and self._locks[chat_id].locked()
        active = [t for t in self._turns.values() if t.status in _ACTIVE]
        return {"chat_id": chat_id, "busy": locked,
                "external_busy": locked and not any(t.status == "running" for t in active),
                "active_turn_id": active[-1].id if active else None}

    # ── internals ─────────────────────────────────────────────────────────────

    async def _run_turn(self, turn: Turn, chat_id: str) -> None:
        lock = self._locks[chat_id]
        try:
            if lock.locked():
                turn.add("queued")
            async with lock:
                turn.status = "running"
                turn.add("started")
                await self._mirror_safely(chat_id, "user", turn.text)
                injected: list = []
                token = turn_events.bind(lambda ev: self._on_event(turn, ev))
                try:
                    reply = await self._run(turn.text, chat_id, on_context_assembled=injected.append,
                                            **turn.run_kwargs)
                finally:
                    turn_events.reset(token)
                self._record_usage(chat_id, injected, reply)
            turn.reply = reply if reply and reply.strip() else _EMPTY_TEXT
            turn.status = "done"
            turn.add("reply", text=turn.reply)
            await self._mirror_safely(chat_id, "assistant", turn.reply)
        except asyncio.CancelledError:
            turn.status = "cancelled"
            turn.add("cancelled")
            raise
        except Exception:  # noqa: BLE001 - the panel must show a failed turn, not hang
            log.exception("panel chat turn failed turn=%s", turn.id)
            turn.status = "error"
            turn.add("error", message=_ERROR_TEXT)

    @staticmethod
    def _on_event(turn: Turn, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "tool_start":
            turn.add("tool_start", id=ev.get("id"), name=ev.get("name"), args=_cut(ev.get("args", "")))
        elif kind == "tool_end":
            result = ev.get("result", "")
            result = result if isinstance(result, str) else str(result)
            turn.add("tool_end", id=ev.get("id"), name=ev.get("name"), chars=len(result),
                     ok=not result.lstrip().startswith('{"error"'), preview=_cut(result))

    async def _mirror_safely(self, chat_id: str, role: str, text: str) -> None:
        """Telegram copy of a panel message; a Telegram failure never fails or delays the panel turn's result."""
        if self._mirror is None:
            return
        try:
            await self._mirror(chat_id, role, text)
        except Exception:  # noqa: BLE001
            log.warning("panel chat: Telegram mirror failed role=%s", role, exc_info=True)

    def _record_usage(self, chat_id: str, injected: list, reply: str) -> None:
        if self._log_usage is None:
            return
        try:
            self._log_usage(chat_id, injected[-1] if injected else None, reply or "")
        except Exception:  # noqa: BLE001 - observability must never cost the turn
            log.exception("panel chat: usage logging failed")

    def _prune(self) -> None:
        while len(self._turns) > self._max_turns:
            oldest = next((t for t in self._turns.values() if t.status not in _ACTIVE), None)
            if oldest is None:
                break
            del self._turns[oldest.id]
