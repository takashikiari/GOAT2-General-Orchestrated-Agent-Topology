"""GOAT 2.0 personality, user profile loading, and conversational response handler.

All conversational responses have CORE_TOOLS (FILE_TOOLS + MEMORY_TOOLS) available.
The LLM autonomously decides when to invoke tools based on semantic intent —
no keyword-based routing, all messages have equal tool access.

REGISTRY INJECTION (PHASE 4):
=============================
direct_response() and conv_result() now require `registry` parameter.
Uses registry.settings.agents.get("tool_caller") and registry tools.

ONBOARDING (PHASE 5):
=====================
First-session users receive a welcome message + adaptive hints for the first 3 turns.
A persistent flag `onboarding_done` in working memory prevents repeated welcome.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final

from config.roles import GOAT_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.identity")

from supervisor.logging.source_types import TaggedResult
from tools.tool_runner import _call_with_tools
from supervisor.types import Plan, SupervisorResult

__all__ = [
    "GOAT_SYSTEM",
    "load_user_profile",
    "direct_response",
    "conv_result",
    "check_onboarding_done",
    "set_onboarding_done",
]

GOAT_SYSTEM: Final[str] = (
    "You are GOAT — a multi-agent supervisor with persistent memory and a DAG execution engine. "
    "You orchestrate specialized agents (researcher, coder, critic, tool_caller, memory) via DAG. "
    "For tasks requiring memory queries (Redis/ChromaDB/Letta) or web search — "
    "use the available tools directly. Do not hallucinate memory data. "
    "Memory tools (all 16): memory_search, memory_get, memory_store, memory_delete, memory_update, "
    "memory_timeline, memory_recent, memory_debug_trace, memory_direct_query, memory_last_write, "
    "memory_count, memory_ttl, memory_embedding, memory_export, memory_promote, memory_auto_promote. "
    "Web search: web_search. "
    "Mirror the user's language, tone, and register. "
    "No filler, no preamble, no apologies, no sign-offs. Never end with a question. "
    "For memory queries (redis, chroma, letta, memory check): if [Memory] block is present in context, "
    "report from it directly. If [Memory] is empty, state that memory is empty — never invent content. Never lie."
)
_PROFILE_KEY:  Final[str] = "human"
_BLOCKED_KEYS: Final[frozenset[str]] = frozenset({
    "agent_id", "passage_id", "search_key", "limit", "offset", "score", "source",
    "memory_type", "ttl", "count", "timestamp", "created_at", "updated_at"})

# ── Onboarding constants ──
_ONBOARDING_KEY: Final[str] = "onboarding_done"
_WELCOME_MESSAGE: Final[str] = (
    "\n\n"
    "\u250C\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n"
    "\u2502  \U0001F410 GOAT \u2014 always ready                          \u2502\n"
    "\u2502                                                     \u2502\n"
    "\u2502  I can read files, search the web, write code,       \u2502\n"
    "\u2502  check memory, analyze, compare, implement.          \u2502\n"
    "\u2502                                                     \u2502\n"
    "\u2502  Just tell me what you need.                         \u2502\n"
    "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518"
)

# Adaptive hints for the first 3 turns (rotating)
_HINTS: Final[list[str]] = [
    "\n\n\U0001F410 I can read any file in the workspace \u2014 just tell me which one.",
    "\n\n\U0001F410 I search the web in real time \u2014 give me a query.",
    "\n\n\U0001F410 I can write code, analyze, compare \u2014 tell me what you need.",
]


# ── Public onboarding helpers ──

async def check_onboarding_done(mm: MemoryManager | None) -> bool:
    """Check if onboarding has been completed in working memory (Redis).

    Returns True if the flag exists and is truthy, False otherwise.
    Safe when mm is None (returns True — no memory = assume done).
    """
    if mm is None:
        return True
    try:
        record = await mm.get(GOAT_ROLE, _ONBOARDING_KEY)
        if record and record.get("content"):
            return record["content"].strip().lower() == "true"
        return False
    except Exception:
        return False


async def set_onboarding_done(mm: MemoryManager | None) -> None:
    """Persist the onboarding_done flag to working memory (Redis).

    Safe when mm is None (no-op).
    """
    if mm is None:
        return
    try:
        await mm.store(GOAT_ROLE, _ONBOARDING_KEY, "true")
    except Exception:
        pass


# ── Internal helpers ──

def _build_welcome_message(turn: int, onboarding_done: bool) -> str:
    """Build the onboarding hint to append to the first response.

    Args:
        turn: Current turn number (1-based).
        onboarding_done: Whether the welcome message was already shown.

    Returns:
        Welcome message string if applicable, empty string otherwise.
    """
    if onboarding_done:
        return ""
    if turn == 1:
        return _WELCOME_MESSAGE
    return ""


def _build_adaptive_hint(turn: int, onboarding_done: bool) -> str:
    """Build an adaptive hint for the first 3 turns.

    Args:
        turn: Current turn number (1-based).
        onboarding_done: Whether onboarding is complete.

    Returns:
        Hint string if applicable, empty string otherwise.
    """
    if onboarding_done:
        return ""
    if 2 <= turn <= 4:
        idx = turn - 2  # turn 2 → hint 0, turn 3 → hint 1, turn 4 → hint 2
        if idx < len(_HINTS):
            return _HINTS[idx]
    return ""


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
        return await mm.get_block(GOAT_ROLE, _PROFILE_KEY) or ""
    except Exception:
        return ""


def _system_with_profile(profile: str, summary: str = "", style: str = "") -> str:
    """Build system prompt: GOAT identity + optional behavior style + filtered profile + summary."""
    from supervisor.behavior.behavior_mirror import mirror_instruction
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
    messages: list[dict[str, str]],
    profile: str,
    registry: "Registry",
    summary: str = "",
    mem_ctx: str = "",
    style: str = "",
    turn: int = 1,
    onboarding_done: bool = True,
) -> TaggedResult:
    """Conversational reply with CORE_TOOLS (FILE_TOOLS + MEMORY_TOOLS) always available.

    The LLM autonomously decides when to invoke tools based on semantic intent.
    No keyword-based routing — all messages have equal tool access regardless of formatting.
    This enables proper handling of conversational requests like 'Goat! Cite\u0219te changelogs...'
    which require file_read access even without explicit command syntax.

    ONBOARDING (PHASE 5):
    =====================
    - First session (onboarding_done=False, turn=1): appends welcome message
    - Turns 2-4 (onboarding_done=False): appends adaptive hints
    - After turn 4: no hints (normal operation)

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings and registry tools.
    """
    from tools import WEB_SEARCH
    _settings = registry.settings
    # GOAT conversational: 16 memory tools + web_search — NO file tools, NO shell
    _memory_tools = registry.memory_tools
    _memory_manager = registry.memory_manager
    sys_content = _system_with_profile(profile, summary, style)
    if mem_ctx:
        sys_content = sys_content + "\n" + mem_ctx

    # Append onboarding content to the system message
    onboarding_content = _build_welcome_message(turn, onboarding_done)
    if not onboarding_content:
        onboarding_content = _build_adaptive_hint(turn, onboarding_done)
    if onboarding_content:
        sys_content = sys_content + onboarding_content

    sys_msg = {"role": "system", "content": sys_content}
    return await _call_with_tools(
        _settings.agents.get("tool_caller"),
        [sys_msg, *messages],
        _memory_tools + [WEB_SEARCH],
        temperature=0.7,
        tool_choice="auto",
        memory_manager=_memory_manager,
    )


async def conv_result(
    intent: str,
    messages: list[dict[str, str]],
    profile: str,
    summary: str,
    mem_ctx: str,
    t0: float,
    registry: "Registry",
    style: str = "",
    turn: int = 1,
    onboarding_done: bool = True,
) -> SupervisorResult:
    """Return a SupervisorResult from a direct LLM response with full conversation history.

    ONBOARDING (PHASE 5):
    =====================
    - First session (onboarding_done=False, turn=1): appends welcome message
    - Turns 2-4 (onboarding_done=False): appends adaptive hints
    - After turn 4: no hints (normal operation)

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter for dependency injection.
    """
    tagged = await direct_response(
        messages, profile, registry, summary, mem_ctx, style,
        turn=turn, onboarding_done=onboarding_done,
    )
    return SupervisorResult(
        intent=intent, plan=Plan(tasks=[]), results={},
        critique="", summary=tagged.content,
        sources={"conv": tagged.source},
        total_duration_s=time.monotonic() - t0,
    )
