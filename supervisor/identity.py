"""GOAT 2.0 personality, user profile loading, and conversational response handler.

All conversational responses have CORE_TOOLS (FILE_TOOLS + MEMORY_TOOLS) available.
The LLM autonomously decides when to invoke tools based on semantic intent —
no keyword-based routing, all messages have equal tool access.
"""
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
    "You are GOAT — a multi-agent supervisor with persistent memory and a DAG execution engine. "
    "You orchestrate specialized agents (researcher, coder, critic, tool_caller, memory) via DAG. "
    "For any task requiring file access, memory queries (Redis/ChromaDB/Letta), or web search — "
    "use the available tools directly. Do not hallucinate file contents or memory data. "
    "Memory tools: memory_search, memory_recent, memory_get, memory_timeline. "
    "File tools: file_read, file_write, file_list, file_search, file_grep, file_info. "
    "Mirror the user's language, tone, and register. "
    "No filler, no preamble, no apologies, no sign-offs. Never end with a question. For memory queries (redis, chroma, letta, memory check): if [Memory] block is present in context, report from it directly. If [Memory] is empty, state that memory is empty — never invent content. Never lie."
)
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
    """Conversational reply with CORE_TOOLS (FILE_TOOLS + MEMORY_TOOLS) always available.

    The LLM autonomously decides when to invoke tools based on semantic intent.
    No keyword-based routing — all messages have equal tool access regardless of formatting.
    This enables proper handling of conversational requests like 'Goat! Citește changelogs...'
    which require file_read access even without explicit command syntax.
    """
    from tools import MEMORY_TOOLS, FILE_TOOLS
    sys_content = _system_with_profile(profile, summary, style)
    if mem_ctx:
        sys_content = sys_content + "

" + mem_ctx
    sys_msg = {"role": "system", "content": sys_content}
    return await _call_with_tools(
        settings.agents.get("tool_caller"),
        [sys_msg, *messages],
        MEMORY_TOOLS + FILE_TOOLS,
        temperature=0.7,
        tool_choice="auto",
    )


async def conv_result(
    intent: str, messages: list[dict[str, str]], profile: str,
    summary: str, mem_ctx: str, t0: float, style: str = "",
) -> SupervisorResult:
    """Return a SupervisorResult from a direct LLM response with full conversation history."""
    tagged = await direct_response(messages, profile, summary, mem_ctx, style)
    return SupervisorResult(
        intent=intent, plan=Plan(tasks=[]), results={},
        critique="", summary=tagged.content,
        sources={"conv": tagged.source},
        total_duration_s=time.monotonic() - t0,
    )
