"""Concurrent session startup: load user profile, session summary, behavior style,
and onboarding status.

ONBOARDING (PHASE 5):
=====================
- On first session, onboarding_done flag is absent from working memory.
- init_session() checks the flag and returns it as the 4th tuple element.
- The flag is set to "true" after the first welcome message is delivered
  (by supervisor.py calling set_onboarding_done()).
- This prevents repeated welcome messages across restarts.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.session")

from supervisor.session.history import ConversationHistory, load_session_summary, load_episodic_context
from supervisor.identity import load_user_profile, check_onboarding_done
from supervisor.behavior.behavior_store import load_style

if TYPE_CHECKING:
    from memory.shared import MemoryManager

__all__ = ["init_session"]


async def init_session(mm: MemoryManager | None) -> tuple[str, ConversationHistory, str, bool]:
    """
    Concurrently load user profile, prior-session summary, behavior style,
    and onboarding status on startup.

    Returns (profile_text, ConversationHistory(summary), style_text, onboarding_done).
    Safe when mm is None — returns empty strings, a fresh history, and onboarding_done=True.

    ONBOARDING (PHASE 5):
    =====================
    - onboarding_done is True if the flag exists in working memory (Redis).
    - onboarding_done is False on first-ever session or if the flag is absent.
    - The caller (supervisor.py) is responsible for setting the flag
      via set_onboarding_done() after delivering the welcome message.
    """
    if mm is None:
        return "", ConversationHistory(), "", True  # No memory = assume done
    profile, summary, style, onboarding, episodic = await asyncio.gather(
        _safe(load_user_profile(mm)),
        _safe(load_session_summary(mm)),
        _safe(load_style(mm)),
        _safe_onboarding(mm),
        _safe(load_episodic_context(mm)),
    )
    history = ConversationHistory(summary)
    # Inject episodic context into profile so GOAT knows previous session context
    full_profile = profile
    if episodic:
        full_profile = (profile + "\n\n" + episodic).strip() if profile else episodic

    return full_profile, history, style, onboarding


async def _safe(coro) -> str:
    """Await coro; always return str — '' on error, non-str, or falsy result."""
    try:
        result = await coro
        if isinstance(result, str):
            return result
        if result:
            log.debug("_safe: expected str, got %s — ignoring", type(result).__name__)
        return ""
    except Exception:
        return ""


async def _safe_onboarding(mm: MemoryManager) -> bool:
    """Safely check onboarding status; return False on any error."""
    try:
        return await check_onboarding_done(mm)
    except Exception:
        return False
