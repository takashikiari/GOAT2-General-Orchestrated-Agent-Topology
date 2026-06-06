"""Session persistence: store conversation turns to episodic memory for cross-session recall."""
from __future__ import annotations

from typing import TYPE_CHECKING, Final

from memory.memory_enums import MemoryType

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["store_turn"]

_ROLE:  Final[str] = "user_session"
_LIMIT: Final[int] = 20


async def store_turn(mm: MemoryManager, turn: int, intent: str, summary: str) -> None:
    """Persist one exchange to WORKING, EPISODIC, and LONG_TERM memory."""
    content = f"User: {intent}\nAssistant: {summary}"
    await mm.store(_ROLE, f"turn_{turn:04d}", content, memory_type=MemoryType.EPISODIC)
    await mm.store(_ROLE, f"turn_{turn:04d}", content, memory_type=MemoryType.WORKING)
    await mm.store(_ROLE, f"turn_{turn:04d}", content, memory_type=MemoryType.LONG_TERM)
