"""GOAT 2.0 personality, user profile, and onboarding helpers.

The single GOAT LLM call lives in ``supervisor.pipeline.goat_call``
and supersedes the old two-call flow (decision + tool-enabled
reply). This module retains everything that still has a caller:
``GOAT_SYSTEM`` (the base system prompt), ``load_user_profile``,
the ``_system_with_profile`` builder used by ``goat_call``,
and the onboarding flag helpers (``check_onboarding_done`` /
``set_onboarding_done``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from config.roles import GOAT_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.identity")

__all__ = [
    "GOAT_SYSTEM",
    "load_user_profile",
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
    "No filler, no preamble, no apologies, no sign-offs. Never end with a question. Never repeat or echo the user message. Always write a visible text response after tool calls — never return empty. "
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
    """Build system prompt: GOAT identity + optional behavior style + filtered profile + summary.

    Used by ``supervisor.pipeline.goat_call._build_system_prompt``
    to assemble the single GOAT LLM call's system message. The
    onboarding block (welcome / adaptive hints) is appended by the
    caller via ``identity_onboarding._build_welcome_message`` and
    ``_build_adaptive_hint`` after this base is built.
    """
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
