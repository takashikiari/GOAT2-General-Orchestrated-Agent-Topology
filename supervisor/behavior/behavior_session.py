"""Session behavior lifecycle: profile load + session-end style persistence.

REGISTRY INJECTION (PHASE 4):
=============================
finalize_behavior() now requires `registry` parameter.
Uses registry.settings.letta.base_url for logging.

PROFILE LOADING:
================
``get_profile(mm)`` returns the active ``BehaviorProfile`` (the parsed
'dict' form) from the Letta 'persona' block, or ``empty_profile()``
when Letta is unreachable / the block is the initial agent description.
This is the per-conversation identity that GoatContext injects into
the working-memory block — GOAT adapts to it on every call.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from supervisor.behavior.behavior_analyzer import analyze_style
from supervisor.behavior.behavior_profile import BehaviorProfile, empty_profile
from supervisor.behavior.behavior_store import load_style, save_style

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from supervisor.session.history import ConversationHistory
    from config.registry import Registry

__all__ = ["finalize_behavior", "get_profile"]

log = logging.getLogger("goat2.supervisor.behavior")


async def get_profile(mm: "MemoryManager | None") -> BehaviorProfile:
    """Return the active ``BehaviorProfile`` from the Letta 'persona' block.

    The profile is the parsed-form dict (``{formality, tone, ...}``)
    ready to inject into GoatContext. Returns ``empty_profile()``
    when mm is None, Letta is unreachable, or the block holds the
    initial agent description (no recognized fields).

    Pure read; never raises. The raw text is loaded via
    ``behavior_store.load_style`` and parsed via
    ``behavior_profile.deserialize``.
    """
    if mm is None:
        return empty_profile()
    try:
        text = await load_style(mm)
        if not text:
            return empty_profile()
        from supervisor.behavior.behavior_profile import deserialize
        profile = deserialize(text)
        if not profile:
            log.debug("get_profile: no recognized fields — returning empty")
            return empty_profile()
        log.debug("get_profile: %d field(s) loaded", len(profile))
        return profile
    except Exception as exc:  # noqa: BLE001 — never raise from a profile read
        log.warning("get_profile: load failed — %s", exc)
        return empty_profile()


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
