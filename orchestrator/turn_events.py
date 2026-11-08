"""orchestrator.turn_events — optional per-turn listener for live tool activity.

The admin panel chat binds a listener around ``orchestrator.run()``; ``Orchestrator._call_tool`` emits one
``tool_start`` and one ``tool_end`` event per tool call. The listener lives in a ContextVar, so it follows the
asyncio task that runs the turn and concurrent turns (Telegram, DAG completions) never see each other's events.
With no listener bound, ``emit`` does nothing.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Callable

from utils.logging.setup import get_logger

log = get_logger(__name__)

Listener = Callable[[dict], None]
_listener: ContextVar[Listener | None] = ContextVar("turn_event_listener", default=None)

__all__ = ["bind", "reset", "emit"]


def bind(listener: Listener) -> Token:
    """Route this task's events to ``listener``; pass the returned token to ``reset``."""
    return _listener.set(listener)


def reset(token: Token) -> None:
    _listener.reset(token)


def emit(event: dict) -> None:
    """Deliver ``event`` to the bound listener. A failing listener must never cost the turn."""
    listener = _listener.get()
    if listener is None:
        return
    try:
        listener(event)
    except Exception:  # noqa: BLE001
        log.exception("turn_events: listener failed for %s", event.get("type"))
