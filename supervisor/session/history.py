"""Conversation history — in-memory rolling buffer of user +
assistant messages, plus a session-end summary.

Pure Python data structure. The supervisor owns one instance
per session. No I/O, no LLM. Persistence (Redis / disk) is the
supervisor's responsibility, not this class's.

USAGE:
    history = ConversationHistory()
    history.add_user("Hello")
    history.add_assistant("Hi there")
    history.messages  # → [{"role": "user", "content": "Hello"}, ...]

Design:
  - Bounded by ``MAX_MESSAGES`` (loaded from
    ``config/memory.toml [working].max_history_messages``; falls
    back to a safe default). Older messages roll off the front.
  - ``add_user`` / ``add_assistant`` append a single message.
  - ``summary`` is a session-level string written by the
    supervisor at session end.
"""
from __future__ import annotations

import logging
from typing import Final

__all__ = ["ConversationHistory", "MAX_MESSAGES"]

log = logging.getLogger("goat2.supervisor.session.history")

_DEFAULT_MAX_MESSAGES: Final[int] = 200


def load_max_messages() -> int:
    """Read ``max_history_messages`` from config/memory.toml [working].

    Returns the configured cap, or ``_DEFAULT_MAX_MESSAGES`` when
    the file / section is missing. Cached at import time.
    """
    try:
        from config.modular_loader import load_memory_config
        section = (load_memory_config() or {}).get("working", {}) or {}
        raw = section.get("max_history_messages")
        if raw is not None:
            return int(raw)
    except (TypeError, ValueError):
        log.debug("history: max_history_messages not int — using default")
    except Exception as exc:  # noqa: BLE001
        log.debug("history: max_history_messages load skipped: %s", exc)
    return _DEFAULT_MAX_MESSAGES


MAX_MESSAGES: Final[int] = load_max_messages()


class ConversationHistory:
    """Rolling in-memory buffer of conversation messages.

    Attributes:
        messages: List of ``{"role": ..., "content": ...}`` dicts.
            Newest message is at the end of the list.
        summary: Optional session-end summary (set externally).
    """

    __slots__ = ("_messages", "summary")

    def __init__(self, summary: str = "") -> None:
        self._messages: list[dict[str, str]] = []
        self.summary: str = summary

    @property
    def messages(self) -> list[dict[str, str]]:
        """Read-only view of the current message list (copy on read)."""
        return list(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def add_user(self, content: str) -> None:
        """Append a user message; trim to ``MAX_MESSAGES`` from the front."""
        self._append({"role": "user", "content": content or ""})

    def add_assistant(self, content: str) -> None:
        """Append an assistant message; trim to ``MAX_MESSAGES`` from the front."""
        self._append({"role": "assistant", "content": content or ""})

    def clear(self) -> None:
        """Drop all in-memory messages (keeps ``summary``)."""
        self._messages.clear()

    def _append(self, msg: dict[str, str]) -> None:
        self._messages.append(msg)
        if len(self._messages) > MAX_MESSAGES:
            # Roll the oldest off the front.
            del self._messages[: len(self._messages) - MAX_MESSAGES]
