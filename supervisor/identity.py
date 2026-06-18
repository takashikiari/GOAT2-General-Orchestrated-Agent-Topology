"""GOAT 2.0 personality, user profile, and onboarding helpers.

The single GOAT LLM call lives in ``supervisor.pipeline.goat_call``
and supersedes the old two-call flow (decision + tool-enabled
reply). This module retains everything that still has a caller:
``GOAT_SYSTEM`` (the base system prompt — operational rules only,
no personality), ``load_user_profile``, the ``_system_with_profile``
builder used by ``goat_call``, and the onboarding flag helpers
(``check_onboarding_done`` / ``set_onboarding_done``).

Personality, style, and capabilities are NOT hard-coded here. They
are injected dynamically by ``_system_with_profile`` from three
sources: the Letta core-memory persona block (per-conversation
identity), the behavior-style profile (per-session tone / language),
and ``GoatContext.available_tools`` (per-registry capability list).
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

# Operational rules only. Personality / style / capabilities are
# injected dynamically by ``_system_with_profile`` from Letta, the
# behavior-style profile, and GoatContext (registry) respectively.
# Each rule below is a hard constraint the LLM must follow on every
# call; everything else is data, not identity.
GOAT_SYSTEM: Final[str] = (
    "1. Never invent facts — use a tool, working memory, or the [Memory] block; report from it directly when present.\n"
    "2. Use tools for any information retrieval; never rely on training data for facts.\n"
    "3. Respond in the user's language; mirror their tone when the style profile says so.\n"
    "4. Never repeat or echo the user's message; never end with a question; no filler or preamble.\n"
    "5. After tool calls, always write a visible text response — never return empty.\n"
    "6. Context entries are labeled [FRESH/RECENT/OLD][CONV/DAG/GOAT]. "
    "Prioritize [FRESH][CONV]. Treat [OLD][DAG] as potentially stale — verify before using."
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
