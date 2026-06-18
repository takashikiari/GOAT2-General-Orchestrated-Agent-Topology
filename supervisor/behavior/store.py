"""Behavior-style persistence — read/write the Letta
``persona`` core-memory block.

Pure orchestration: ``load_style`` / ``save_style`` are thin
async wrappers over ``MemoryManager.get_block`` / ``set_block``.
No LLM, no regex, no I/O of their own.

USAGE:
    from supervisor.behavior.store import load_style, save_style

    text: str = await load_style(mm)
    ok:   bool = await save_style(mm, "formality: casual\\ntone: technical")

FAILURE MODES:
    - mm is None → ``""`` / ``False``.
    - Letta unreachable → ``""`` / ``False`` (log WARNING).
    - Stored text doesn't parse as a profile → ``""`` (defensive:
      a fresh Letta instance has the initial agent description,
      not a profile — treat as empty so callers can use the
      ``empty_profile()`` default).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.behavior.store")

__all__ = ["BLOCK_LABEL", "load_style", "save_style"]

# Block name in Letta's core-memory. All GOAT agents share the
# same persona block — only one profile per agent.
BLOCK_LABEL: Final[str] = "persona"


async def load_style(mm: "MemoryManager | None") -> str:
    """Read the GOAT ``persona`` block from Letta.

    Returns the raw ``key: value`` text (suitable for re-feeding
    into ``save_style`` and for rendering via
    ``behavior.mirror.mirror_instruction``). Returns ``""`` when
    mm is None, Letta is unreachable, or the block holds the
    initial agent description (no recognized fields).
    """
    if mm is None:
        return ""
    try:
        text = await mm.get_block("goat", BLOCK_LABEL) or ""
    except Exception as exc:
        log.warning("load_style: get_block failed: %s", exc)
        return ""
    if not text:
        return ""
    # Defensive: if the block doesn't parse as a profile, treat
    # it as empty (the agent-description case).
    try:
        from supervisor.behavior.profile import deserialize
        if not deserialize(text):
            log.debug("load_style: block is not a profile — returning empty")
            return ""
    except Exception:  # noqa: BLE001
        return ""
    return text


async def save_style(mm: "MemoryManager | None", style: str) -> bool:
    """Overwrite the GOAT ``persona`` block with ``style``.

    Returns True on success, False on any failure (mm None,
    empty style, Letta unreachable / write rejected). The
    boolean is suitable for the caller's
    ``if written: refresh_style(...)`` pattern.
    """
    if mm is None or not (style and style.strip()):
        return False
    try:
        ok = await mm.set_block("goat", BLOCK_LABEL, style)
    except Exception as exc:
        log.error("save_style: set_block raised: %s", exc)
        return False
    if ok:
        log.info("save_style: persona written (%d chars)", len(style))
    else:
        log.error("save_style: set_block returned False — Letta unreachable?")
    return ok