"""MemoryManager turn-based promotion (working → episodic → long-term).

Extracted from ``memory_manager.py`` to keep that file under the 260-line
ceiling. Promotion rules:

- Turn 2+ (messages >= 4): WORKING → EPISODIC, keep_source=True
- Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, keep_source=False

Each entry is gated by ``promote_with_guard`` so duplicates and content
that fails the ``PollutionGuard`` are skipped (logged at DEBUG).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.shared.memory_enums import MemoryType

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.shared")

__all__ = ["run_promote_turns"]


async def run_promote_turns(self: "MemoryManager", agent_role: str, turn_count: int) -> None:
    """Background promotion task based on turn count.

    Args:
        agent_role: Role namespace (e.g. 'user_session').
        turn_count: Current number of messages in history.
    """
    try:
        if turn_count >= 4:
            keys = await self.working.backend.keys(agent_role)
            for key in keys:
                if key.startswith("turn_"):
                    await self.promote_with_guard(
                        agent_role, key,
                        from_type=MemoryType.WORKING,
                        to_type=MemoryType.EPISODIC,
                        keep_source=True,
                    )
            log.debug("promote_turns: working → episodic for %d keys", len(keys))

        if turn_count >= 6:
            entries = await self.episodic.search(agent_role, "turn", limit=10)
            for entry in entries:
                key = entry.key if hasattr(entry, "key") else entry.get("id", "")
                if key.startswith("turn_"):
                    await self.promote_with_guard(
                        agent_role, key,
                        from_type=MemoryType.EPISODIC,
                        to_type=MemoryType.LONG_TERM,
                        keep_source=False,
                    )
            log.debug("promote_turns: episodic → long_term completed")
    except Exception as e:
        log.warning("promote_turns: background task failed: %s", e)
