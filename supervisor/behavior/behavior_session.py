"""Session-end behavior lifecycle: analyze user turns and persist updated style profile.

REGISTRY INJECTION (PHASE 4):
=============================
finalize_behavior() now requires `registry` parameter.
Uses registry.settings.letta.base_url for logging.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from supervisor.behavior.behavior_analyzer import analyze_style
from supervisor.behavior.behavior_store import save_style

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from supervisor.session.history import ConversationHistory
    from config.registry import Registry

__all__ = ["finalize_behavior"]

log = logging.getLogger("goat2.supervisor.behavior")


async def finalize_behavior(
    mm: MemoryManager | None,
    history: ConversationHistory | None,
    current_style: str,
    registry: "Registry",
) -> str:
    """
    Extract user turns from history, infer communication style, persist to Letta 'persona' block.
    Returns updated style text; returns current_style unchanged on failure or when mm is None.
    Only writes to Letta when the profile changes (skips identical result).

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.letta.base_url for logging.
    """
    _settings = registry.settings
    if mm is None or history is None:
        log.debug("finalize_behavior: skipped (mm=%s, history=%s)", mm is None, history is None)
        return current_style
    turns = [m["content"] for m in history.messages if m["role"] == "user"]
    log.info("finalize_behavior: %d user turn(s); existing style: %s",
             len(turns), repr(current_style[:80]) if current_style else "<empty>")
    style = await analyze_style(turns, registry, current_style)
    if not style:
        log.info("finalize_behavior: analyze returned empty profile — nothing to write")
        return current_style
    if style == current_style:
        log.info("finalize_behavior: style unchanged — skipping Letta write")
        return current_style
    log.info("finalize_behavior: style updated, writing to Letta %s (goat/persona):\n%s",
             _settings.letta.base_url, style)
    saved = await save_style(mm, style)
    if not saved:
        log.error(
            "finalize_behavior: persona block NOT written — "
            "set_block(goat, persona) failed. Letta URL: %s. "
            "Check server logs or run: curl %s/v1/health",
            _settings.letta.base_url, _settings.letta.base_url,
        )
    return style
