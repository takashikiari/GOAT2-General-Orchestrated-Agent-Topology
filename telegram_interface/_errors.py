"""telegram_interface._errors — the Application's error handler (GOAT + Laris, 2026-11-17).

Without one, python-telegram-bot logs every error as "No error handlers are registered, logging exception." with a
full traceback. Live (16-17.11) all of them were network blips on polling — telegram.error.NetworkError "Bad
Gateway", httpcore.ReadError — which the library retries by itself: nothing was lost, but each one looked like an
ERROR. Now:

- exactly NetworkError, or TimedOut (transient, retried by PTB): ONE WARNING line, no traceback;
- anything else: ERROR with context (update type, chat id, the update's text start) and the traceback.

No message is sent to the chat: for a network error it would fail or spam; a handler's own failure already answers
the user with its error reply.
"""
from __future__ import annotations

from typing import Any

from telegram.error import NetworkError, TimedOut

from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["on_error"]


def _context(update: Any) -> str:
    if update is None:
        return "update=None"
    chat = getattr(getattr(update, "effective_chat", None), "id", None)
    message = getattr(update, "effective_message", None)
    text = (getattr(message, "text", None) or "")[:60]
    return f"update={type(update).__name__} chat={chat} text={text!r}"


async def on_error(update: object, context: Any) -> None:
    """Application.add_error_handler target: transient network errors as one line, the rest with traceback."""
    err = getattr(context, "error", None)
    # exactly NetworkError (PTB wraps httpx/httpcore failures and 'Bad Gateway' in it) or TimedOut. NOT its other
    # subclasses: BadRequest, Forbidden, Conflict, InvalidToken... are NetworkError too in PTB, and they are real
    # errors (a bad parse_mode, a blocked bot), not blips
    if type(err) is NetworkError or isinstance(err, TimedOut):
        log.warning("telegram network error (retried by the library) %s: %s: %s", _context(update),
                    type(err).__name__, err)
        return
    log.error("telegram handler error %s: %s: %s", _context(update), type(err).__name__, err, exc_info=err)
