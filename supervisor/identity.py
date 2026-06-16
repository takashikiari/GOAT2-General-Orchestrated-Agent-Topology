"""GOAT 2.0 personality, user profile, and (legacy) tool-enabled reply.

The single GOAT LLM call lives in ``supervisor.pipeline.goat_call``
and supersedes ``direct_response``/``conv_result`` for the
turn-by-turn flow. The functions here are kept for any external
caller that imports them; new code should use ``goat_call.goat_turn``.

Tools used by the single call are wired in
``goat_call._collect_goat_tools`` — the same set the old
``direct_response`` had (memory, web, dag, goat_skills, dynamic).
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

# ── Onboarding helpers (constants + builders live in identity_onboarding.py) ──
from supervisor.identity_onboarding import (  # noqa: E402
    _ONBOARDING_KEY,
    _build_welcome_message,
    _build_adaptive_hint,
)


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
    import datetime as _dt
    _now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _ts = "\nCurrent date and time: " + _now + " (Romania/Bucharest timezone)."


    parts = [GOAT_SYSTEM, _ts]
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
    goat_session_id: str = "",
    supervisor=None,
) -> TaggedResult:
    """Tool-enabled conversational reply (legacy). See goat_call.goat_turn.

    The LLM autonomously decides when to invoke tools. No keyword routing.

    Kept for external callers; the single-call architecture in
    goat_call supersedes this for the turn-by-turn flow.
    """
    from tools import WEB_SEARCH
    from tools.system import READ_LOGS
    from tools.dag import make_dag_tools
    _settings = registry.settings
    # GOAT conversational tool surface — clearly separated from DAG tools.
    #
    # GOAT CORE_TOOLS = goat_skills (mouse/keyboard/screen/shell/browser/clipboard)
    #                 + memory_all_tiers (working + episodic + long_term)
    #                 + web_search
    #                 + read_logs
    #                 + dag_tools (start_dag, query_dag_status, control_dag, list_dag_sessions)
    #                 + dynamic_tools (hot-reloaded from tools/dynamic/)
    #
    # GOAT does NOT have file_tools directly — file operations go through
    # the DAG (workflow.py injects FILE_TOOLS into DAG agents only).
    #
    # Hot-reload contract: the lists below are captured at call time
    # (not at module init), but they reference the SAME list object
    # that the registry exposes. The ToolsWatcher reloads by mutating
    # the contents in place via ``ServiceRegistry.update_tools`` —
    # ``slot[:] = new_tools`` — so the locals below stay valid across
    # reloads. Do NOT rebind ``registry.memory_tools = ...`` from
    # elsewhere; that would desync the captured reference.
    _memory_tools      = registry.memory_tools
    _memory_manager    = registry.memory_manager
    _dag_tools         = make_dag_tools(_memory_manager, goat_session_id=goat_session_id, supervisor=supervisor)
    _goat_skills_tools = registry.goat_skills_tools
    _dynamic_tools     = getattr(registry, "dynamic_tools", []) or []
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
        # GOAT CORE_TOOLS — no FILE_TOOLS, no DAG_MEMORY_TOOLS.
        # File ops are dispatched via the DAG; DAG agents get the
        # sandboxed working-tier memory tools only.
        _memory_tools
        + [WEB_SEARCH, READ_LOGS]
        + _dag_tools
        + _goat_skills_tools
        + _dynamic_tools,
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
    goat_session_id: str = "",
    supervisor=None,
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
        goat_session_id=goat_session_id,
        supervisor=supervisor,
    )
    return SupervisorResult(
        intent=intent, plan=Plan(tasks=[]), results={},
        critique="", summary=tagged.content,
        sources={"conv": tagged.source},
        total_duration_s=time.monotonic() - t0,
    )
