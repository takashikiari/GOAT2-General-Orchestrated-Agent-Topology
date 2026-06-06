"""Session persistence: store conversation turns and DAG results to WORKING tier (Redis).

All turns (conversational and DAG) are stored to WORKING memory for cross-turn access.
This bridges DAG execution results into the conversational context layer.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Final

from memory.memory_enums import MemoryType

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["store_turn"]

_ROLE:  Final[str] = "user_session"


async def store_turn(mm: MemoryManager, turn: int, intent: str, summary: str) -> None:
    """Persist one exchange to WORKING tier (Redis) only.

    Both conversational responses and DAG results are stored here. This enables
    the conversational path to access prior DAG execution results via memory tools.
    GOAT supervisor handles promotion to EPISODIC/LONG_TERM at session end.
    """
    content = f"User: {intent}\nAssistant: {summary}"
    await mm.store(_ROLE, f"turn_{turn:04d}", content, memory_type=MemoryType.WORKING)
