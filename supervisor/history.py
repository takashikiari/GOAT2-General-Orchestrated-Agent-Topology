"""In-session conversation history and cross-session summary for GoatSupervisor."""
from __future__ import annotations

from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["ConversationHistory", "load_session_summary"]

_SUMMARY_KEY  = "session_summary"


class ConversationHistory:
    """Maintains role/content message pairs for the current session."""

    def __init__(self, summary: str = "") -> None:
        self._msgs: list[dict[str, str]] = []
        self.summary: str = summary  # injected into system prompt at call time; not in messages

    def add_user(self, content: str) -> None:
        """Append a user turn."""
        self._msgs.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        """Append an assistant turn."""
        self._msgs.append({"role": "assistant", "content": content})

    @property
    def messages(self) -> list[dict[str, str]]:
        """Return a snapshot of the full message list."""
        return list(self._msgs)

    def as_context(self) -> str:
        """Return recent USER turns only as plain text for DAG planner injection.

        Assistant turns contain DAG execution results (web search, file reads, etc.)
        which should NOT influence planning — only user intent matters for task decomposition.
        Returns last 6 user turns, excluding system and assistant messages.
        """
        turns = [m for m in self._msgs if m["role"] == "user"]
        return "\n".join(f"User: {m['content']}" for m in turns[-6:])

    def as_full_context(self) -> str:
        """Return all turns (user + assistant) for display/memory purposes only.

        Includes assistant responses with DAG results. Do NOT use this for planning —
        use as_context() instead which filters to user turns only.
        """
        turns = [m for m in self._msgs if m["role"] != "system"]
        return "\n".join(f"{m['role'].title()}: {m['content']}" for m in turns[-6:])

    def as_plan_context(self, intent: str, profile: str = "", mem_ctx: str = "") -> str:
        """Build plan-decomposition context: memory recall + profile + history + current intent."""
        parts = []
        if mem_ctx:
            parts.append(mem_ctx)
        if profile:
            parts.append(f"[User: {profile}]")
        ctx = self.as_context()
        if ctx:
            parts.append(f"[Conversation history]\n{ctx}")
        parts.append(intent)
        return "\n".join(parts)


async def load_session_summary(mm: MemoryManager | None) -> str:
    """Retrieve compressed summary of prior sessions from episodic memory; returns '' if absent."""
    if mm is None:
        return ""
    from memory.memory_enums import MemoryType
    try:
        entry = await mm.retrieve(SESSION_ROLE, _SUMMARY_KEY, memory_type=MemoryType.EPISODIC)
        return entry.content if entry else ""
    except Exception:
        return ""
