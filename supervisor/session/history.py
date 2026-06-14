"""In-session conversation history and cross-session summary for GoatSupervisor."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.session")

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager

__all__ = ["ConversationHistory", "load_session_summary", "load_episodic_context"]

_SUMMARY_KEY  = "session_summary"
_EPISODIC_LIMIT = 300

# Display order + labels for episodic compartments in the injected context block.
_COMPARTMENT_LABELS: tuple[tuple[str, str], ...] = (
    ("turns", "Turns"),
    ("preferences", "Preferences"),
    ("corrections", "Corrections"),
    ("dag_results", "DAG Results"),
)


def _strip_dsml(text: str) -> str:
    """Remove DeepSeek DSML markers from text to prevent LLM confusion."""
    # Match full wrapper tags with different content: <tag1>...</tag2>
    text = re.sub(r'<\｜｜DSML｜｜\w+>[^<]*</\｜｜DSML｜｜\w+>', '', text, flags=re.DOTALL)
    # Strip orphaned opening tags
    text = re.sub(r'<\｜｜DSML｜｜[^>]*>', '', text)
    return text.strip()


class ConversationHistory:
    """Maintains role/content message pairs for the current session."""

    def __init__(self, summary: str = "") -> None:
        self._msgs: list[dict[str, str]] = []
        self.summary: str = summary  # injected into system prompt at call time; not in messages

    def add_user(self, content: str) -> None:
        """Append a user turn."""
        self._msgs.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        """Append an assistant turn, stripping DSML markers to prevent LLM confusion."""
        clean = _strip_dsml(content)
        self._msgs.append({"role": "assistant", "content": clean})

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
    from memory.shared.memory_enums import MemoryType
    try:
        entry = await mm.retrieve(SESSION_ROLE, _SUMMARY_KEY, memory_type=MemoryType.EPISODIC)
        return entry.content if entry else ""
    except Exception:
        return ""


def _entry_field(entry: object, name: str, default: str = "") -> str:
    """Read a field from an entry that may be a dict or a MemoryEntry."""
    if isinstance(entry, dict):
        val = entry.get(name, default)
    else:
        val = getattr(entry, name, default)
    return val if isinstance(val, str) else (str(val) if val is not None else default)


async def load_episodic_context(mm: "MemoryManager | None", limit: int = _EPISODIC_LIMIT) -> str:
    """Load episodic entries for session injection, grouped by compartment.

    Loads up to ``limit`` entries (all 300 by default) from the episodic tier and
    renders an ``[Episodic Memory]`` block with one labelled sub-block per
    compartment (Turns / Preferences / Corrections / DAG Results). Best-effort:
    returns '' on any error so session start never breaks.
    """
    if mm is None:
        return ""
    try:
        entries = await mm.episodic.list("user_session", limit=limit)
        if not entries:
            return ""
        buckets: dict[str, list[str]] = {}
        for e in entries:
            content = _entry_field(e, "content")
            if not content:
                continue
            meta = e.get("metadata", {}) if isinstance(e, dict) else getattr(e, "metadata", {})
            comp = str((meta or {}).get("compartment", "")) or "turns"
            key = _entry_field(e, "key")
            date = _entry_field(e, "created_at")[:10]
            buckets.setdefault(comp, []).append(f"- {key} ({date}): {content[:200]}")
        if not buckets:
            return ""
        out = ["[Episodic Memory]"]
        for comp, label in _COMPARTMENT_LABELS:
            if buckets.get(comp):
                out.append(f"[{label}]")
                out.extend(buckets[comp])
        # Any compartment not in the known label list (forward-compat).
        known = {c for c, _ in _COMPARTMENT_LABELS}
        for comp, lines in buckets.items():
            if comp not in known:
                out.append(f"[{comp.title()}]")
                out.extend(lines)
        log.debug("load_episodic_context: %d entries across %d compartments", len(entries), len(buckets))
        return "\n".join(out)
    except Exception as exc:
        log.debug("load_episodic_context failed: %s", exc)
        return ""
