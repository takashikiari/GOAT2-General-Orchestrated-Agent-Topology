"""GOAT 2.0 personality, user profile loading, and conversational response handler."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

from config.settings import settings
from supervisor.source_types import TaggedResult
from supervisor.tool_runner import _call_with_tools
from supervisor.types import Plan, SupervisorResult

__all__ = ["GOAT_SYSTEM", "load_user_profile", "direct_response", "conv_result"]

GOAT_SYSTEM: Final[str] = (
    "You are GOAT — a personal assistant with persistent memory. Mirror the user's language, tone, and register. "
    "No filler, no preamble, no apologies, no sign-offs. Never end with a question.")

_PROFILE_ROLE: Final[str] = "goat"
_PROFILE_KEY:  Final[str] = "human"
_BLOCKED_KEYS: Final[frozenset[str]] = frozenset({
    "agent_id", "passage_id", "search_key", "limit", "offset", "score", "source",
    "memory_type", "ttl", "count", "timestamp", "created_at", "updated_at"})


def _filter_profile(text: str) -> str:
    """Strip technical-metadata key lines from a 'key: value' profile block before display."""
    kept = []
    for line in text.splitlines():
        if ":" in line:
            key = line.partition(":")[0].strip().lower()
            if key in _BLOCKED_KEYS or key.endswith("_id"):
                continue
        kept.append(line)
    return "\n".join(ln for ln in kept if ln.strip())


async def load_user_profile(mm: MemoryManager) -> str:
    """Load user profile from Letta core-memory; returns '' if unavailable or unset."""
    try:
        return await mm.get_block(_PROFILE_ROLE, _PROFILE_KEY) or ""
    except Exception:
        return ""


def _system_with_profile(profile: str, summary: str = "", style: str = "") -> str:
    """Build system prompt: GOAT identity + optional behavior style + filtered profile + summary."""
    from supervisor.behavior_mirror import mirror_instruction
    parts = [GOAT_SYSTEM]
    if style:
        directive = mirror_instruction(style)
        if directive:
            parts.append(f"\n{directive}")
    if profile:
        clean = _filter_profile(profile)
        if clean:
            parts.append(f"\nUser profile:\n{clean}")
    if summary:
        parts.append(f"\nPrevious sessions:\n{summary}")
    return "".join(parts)


async def direct_response(
    messages: list[dict[str, str]], profile: str, summary: str = "",
    mem_ctx: str = "", style: str = "",
) -> TaggedResult:
    """Conversational reply using CORE_TOOLS (FILE_TOOLS + MEMORY_TOOLS) always available.

    FILE_TOOLS includes file_read, file_write, file_create, file_list, file_search,
    file_grep, file_info, file_read_lines, and web_search. Returns a TaggedResult
    so the caller can propagate source provenance.
    """
    from tools import MEMORY_TOOLS, FILE_TOOLS
    sys_msg  = {"role": "system", "content": _system_with_profile(profile, summary, style)}
    ctx_msgs = [{"role": "system", "content": mem_ctx}] if mem_ctx else []
    return await _call_with_tools(
        settings.agents.get("planner"),
        [sys_msg, *ctx_msgs, *messages],
        MEMORY_TOOLS + FILE_TOOLS,
        temperature=0.7,
    )


async def conv_result(
    intent: str, messages: list[dict[str, str]], profile: str,
    summary: str, mem_ctx: str, t0: float, style: str = "",
) -> SupervisorResult:
    """Return a SupervisorResult from a direct LLM response using full conversation history."""
    tagged = await direct_response(messages, profile, summary, mem_ctx, style)
    return SupervisorResult(
        intent=intent, plan=Plan(tasks=[]), results={},
        critique="", summary=tagged.content,
        sources={"conv": tagged.source},
        total_duration_s=time.monotonic() - t0,
    )
