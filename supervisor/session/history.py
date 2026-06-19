"""Conversation history — in-memory rolling buffer of user +
assistant messages, plus a session-end summary.

Pure Python data structure. The supervisor owns one instance
per session. No I/O, no LLM. Persistence (Redis / disk) is the
supervisor's responsibility, not this class's.

USAGE:
    history = ConversationHistory()
    history.add_user("Hello", pending=True)   # buffered
    history.add_assistant("Hi there")          # may be empty — silently skipped
    history.commit_pending()                    # promote buffered user to visible
    # On turn failure:
    history.rollback_pending()                  # discard buffered user

Design:
  - Bounded by ``MAX_MESSAGES`` (loaded from
    ``config/memory.toml [working].max_history_messages``; falls
    back to a safe default). Older messages roll off the front.
  - ``add_user(content, *, pending=False)`` appends a single
    message. When ``pending=True`` the message is buffered and
    only becomes visible after ``commit_pending()`` is called —
    the supervisor uses this to avoid leaving orphan user
    messages in the buffer if the LLM call fails (BUG-015).
  - ``add_assistant(content)`` appends a single message but
    silently skips empty / whitespace / ``None`` content so the
    history never accumulates empty rows (BUG-016).
  - ``commit_pending()`` promotes a pending user turn to the
    visible history. No-op when nothing is pending.
  - ``rollback_pending()`` discards a pending user turn. No-op
    when nothing is pending.
  - ``summary`` is a session-level string written by the
    supervisor at session end.
"""
from __future__ import annotations

import logging
from typing import Final, Optional

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
        _pending: Buffered user message waiting for commit. Not in
            ``messages`` until ``commit_pending()`` is called.
    """

    __slots__ = ("_messages", "summary", "_pending")

    def __init__(self, summary: str = "") -> None:
        self._messages: list[dict[str, str]] = []
        self.summary: str = summary
        self._pending: Optional[dict[str, str]] = None

    @property
    def messages(self) -> list[dict[str, str]]:
        """Read-only view of the current message list (copy on read)."""
        return list(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def add_user(self, content: str, *, pending: bool = False) -> None:
        """Append a user message; trim to ``MAX_MESSAGES`` from the front.

        Args:
            content: The raw user text.
            pending: When True, the message is buffered but not
                committed to ``_messages`` yet. The supervisor uses
                this to avoid leaving orphan user messages in
                history when the LLM call fails (BUG-015). The
                caller MUST follow up with ``commit_pending()``
                on success or ``rollback_pending()`` on failure.
        """
        msg = {"role": "user", "content": content or ""}
        if pending:
            self._pending = msg
            return
        self._append(msg)

    def add_assistant(self, content: Optional[str]) -> None:
        """Append an assistant message; skip empty / whitespace / None.

        An empty assistant row is a sign that the LLM was silent
        (e.g. the antirepeat gate fired, or a tool-schema error
        produced no text). Storing such rows pollutes the history
        the LLM sees on subsequent turns — the model thinks the
        conversation is progressing when it isn't. We log at
        DEBUG and skip (BUG-016).
        """
        if not content or not str(content).strip():
            log.debug(
                "add_assistant: skipped empty content (was %r)",
                type(content).__name__,
            )
            return
        self._append({"role": "assistant", "content": str(content)})

    def commit_pending(self) -> None:
        """Promote a pending user turn to the visible history.

        No-op when nothing is pending. Called by the supervisor
        after a successful LLM invocation.
        """
        if self._pending is None:
            return
        self._append(self._pending)
        self._pending = None

    def rollback_pending(self) -> None:
        """Discard a pending user turn.

        No-op when nothing is pending. Called by the supervisor
        when the LLM invocation raises — prevents the user message
        from staying in the buffer without a matching assistant
        reply (BUG-015).
        """
        if self._pending is None:
            return
        log.debug(
            "rollback_pending: discarded pending user message (%d chars)",
            len(self._pending.get("content", "")),
        )
        self._pending = None

    def clear(self) -> None:
        """Drop all in-memory messages (keeps ``summary``)."""
        self._messages.clear()
        self._pending = None

    def _append(self, msg: dict[str, str]) -> None:
        self._messages.append(msg)
        if len(self._messages) > MAX_MESSAGES:
            # Roll the oldest off the front.
            del self._messages[: len(self._messages) - MAX_MESSAGES]
