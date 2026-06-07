"""Load and save the GOAT behavior-style profile via the Letta 'persona' core-memory block."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from config.roles import GOAT_ROLE

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["load_style", "save_style"]

log = logging.getLogger("goat2.supervisor.behavior")

_BLOCK: Final[str] = "persona"


async def load_style(mm: MemoryManager | None) -> str:
    """
    Read the 'persona' Letta block for the 'goat' agent.
    Returns '' when Letta is unreachable, mm is None, or the block holds the
    initial agent description instead of a real style profile (no recognized fields).
    """
    if mm is None:
        return ""
    try:
        text = await mm.get_block(GOAT_ROLE, _BLOCK) or ""
        if not text:
            return ""
        from supervisor.behavior_profile import deserialize
        profile = deserialize(text)
        if not profile:
            log.debug("load_style: block is not a style profile (initial agent description?) — ignoring")
            return ""
        log.debug("load_style: loaded %d style field(s)", len(profile))
        return text
    except Exception as exc:
        log.warning("load_style: failed reading Letta %s/%s — %s", GOAT_ROLE, _BLOCK, exc)
        return ""


async def save_style(mm: MemoryManager | None, style: str) -> bool:
    """
    Overwrite the 'persona' block with the updated style text.
    Returns True on success, False when skipped or when Letta rejects / is unreachable.
    """
    if mm is None or not style.strip():
        log.debug("save_style: skipped (mm=%s, style_empty=%s)", mm is None, not style.strip())
        return False
    try:
        ok = await mm.set_block(GOAT_ROLE, _BLOCK, style)
        if ok:
            log.info("save_style: behavior profile written to Letta %s/%s (%d chars)",
                     GOAT_ROLE, _BLOCK, len(style))
        else:
            log.error("save_style: set_block returned False — Letta unreachable or write rejected")
        return ok
    except Exception as exc:
        log.error("save_style: exception writing Letta %s/%s — %s", GOAT_ROLE, _BLOCK, exc)
        return False
