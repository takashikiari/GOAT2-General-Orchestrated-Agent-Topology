"""Concurrent session startup: load user profile, session summary, and behavior style."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from supervisor.history import ConversationHistory, load_session_summary
from supervisor.identity import load_user_profile
from supervisor.behavior_store import load_style

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["init_session"]


async def init_session(mm: MemoryManager | None) -> tuple[str, ConversationHistory, str]:
    """
    Concurrently load user profile, prior-session summary, and behavior style on startup.
    Returns (profile_text, ConversationHistory(summary), style_text).
    Safe when mm is None — returns empty strings and a fresh history.
    """
    if mm is None:
        return "", ConversationHistory(), ""
    profile, summary, style = await asyncio.gather(
        _safe(load_user_profile(mm)),
        _safe(load_session_summary(mm)),
        _safe(load_style(mm)),
    )
    return profile, ConversationHistory(summary), style


async def _safe(coro) -> str:
    """Await coro; return '' on any exception or falsy result."""
    try:
        return await coro or ""
    except Exception:
        return ""
