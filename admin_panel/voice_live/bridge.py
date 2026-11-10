"""admin_panel.voice_live.bridge — ``ask_goat``: what the voice model asks GOAT, and the recent chat it starts from.

A request becomes a normal chat turn (``ChatService.submit``), so it shows in the Chat page and in Telegram like any
message and runs on the same orchestrator and per-chat lock. The turn is never cancelled from here: if the voice
session ends first, GOAT finishes and the answer lands in the chat.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from admin_panel.coord_feed import split_conversation, split_tools
from admin_panel.voice_live.provider import HistoryItem
from orchestrator.chat_service import ChatBusy, ChatService, ChatUnavailable
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["GoatBridge", "recent_history"]

_DONE = ("done", "error", "cancelled")


class GoatBridge:
    def __init__(self, service: ChatService, *, poll_seconds: float = 0.25, timeout_seconds: float = 600.0,
                 max_reply_chars: int = 4000) -> None:
        self._service = service
        self._poll, self._timeout, self._max_reply = poll_seconds, timeout_seconds, max_reply_chars
        self.busy = False  # one request at a time per voice session: GOAT is one conversation, one turn at a time

    async def ask(self, request: str, on_event: Callable[[dict], Awaitable[None]],
                  on_refused: Callable[[], None] | None = None) -> str:
        """GOAT's answer as text (or a plain sentence saying why there is none). ``on_event`` gets tool progress;
        ``on_refused`` is called when the request never reached GOAT."""
        refused = on_refused or (lambda: None)
        if self.busy:
            refused()
            return "GOAT is still working on the previous request. Wait for that answer first."
        try:
            turn = self._service.submit(request)
        except ValueError:
            refused()
            return "The request was empty or too long."
        except ChatBusy:
            refused()
            return "GOAT has too many messages waiting. Try again in a moment."
        except ChatUnavailable:
            refused()
            return "GOAT's chat is not available right now."
        self.busy = True
        try:
            return await self._wait(turn.id, on_event)
        finally:
            self.busy = False

    def note(self, text: str) -> bool:
        """Leave ``text`` with GOAT as a normal chat turn without waiting for the answer (the conversation is over).
        False (logged) if the chat would not take it."""
        try:
            self._service.submit(text)
            return True
        except (ValueError, ChatBusy, ChatUnavailable) as exc:
            log.warning("voice_live: the end-of-conversation transcript was not sent (%s)", type(exc).__name__)
            return False

    async def _wait(self, turn_id: str, on_event: Callable[[dict], Awaitable[None]]) -> str:
        seen = 0
        waited = 0.0
        while waited <= self._timeout:
            view = self._service.get(turn_id, after=seen)
            if view is None:
                return "The request was lost."
            for event in view["events"]:
                seen = max(seen, event["seq"])
                if event["type"] == "tool_start":
                    await on_event({"type": "tool", "name": event.get("name"), "status": "running"})
                elif event["type"] == "tool_end":
                    await on_event({"type": "tool", "name": event.get("name"), "status": "done" if event.get("ok") else "failed"})
            if view["status"] in _DONE:
                if view["status"] == "done" and view["reply"]:
                    reply = view["reply"]
                    return reply if len(reply) <= self._max_reply else reply[: self._max_reply] + "…"
                return "GOAT could not finish that request." if view["status"] == "error" else "That request was cancelled."
            await asyncio.sleep(self._poll)
            waited += self._poll
        return "GOAT is taking too long; the answer will appear in the chat when it is ready."


async def recent_history(registry, limit: int = 12, max_chars: int = 600) -> list[HistoryItem]:
    """The last messages of the shared conversation (without agent digests and tool evidence), for the model to start from."""
    service = getattr(registry, "chat_service", None)
    memory = getattr(registry, "working_memory", None)
    chat_id = service.state().get("chat_id") if service is not None else None
    if not chat_id or memory is None:
        return []
    try:
        stored = await memory.get_messages(chat_id)
    except Exception as exc:  # noqa: BLE001 - the conversation can start without its history
        log.warning("voice_live: history unavailable: %s", type(exc).__name__)
        return []
    chat, _ = split_conversation(stored)
    items: list[HistoryItem] = []
    for entry in chat[-limit:]:
        text = entry.get("content") or ""
        role = "model" if entry.get("role") == "assistant" else "user"
        if role == "model":
            _, text = split_tools(text)
        text = text.strip()
        if text:
            items.append(HistoryItem(role, text if len(text) <= max_chars else text[:max_chars] + "…"))
    return items
