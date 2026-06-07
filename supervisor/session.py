"""Session persistence: store conversation turns and DAG results to WORKING tier (Redis).

All turns (conversational and DAG) are stored to WORKING memory for cross-turn access.
This bridges DAG execution results into the conversational context layer.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from memory.memory_enums import MemoryType
from memory.working_record import RecordDict

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["store_turn", "store_dag_result", "retrieve_dag_result"]

_ROLE:  Final[str] = "user_session"


async def store_turn(mm: MemoryManager, turn: int, intent: str, summary: str) -> None:
    """Persist one exchange to WORKING tier (Redis) only.

    Both conversational responses and DAG results are stored here. This enables
    the conversational path to access prior DAG execution results via memory tools.
    GOAT supervisor handles promotion to EPISODIC/LONG_TERM at session end.
    """
    content = f"User: {intent}\nAssistant: {summary}"
    await mm.store(_ROLE, f"turn_{turn:04d}", content, memory_type=MemoryType.WORKING)


async def store_dag_result(mm: MemoryManager, session_id: str, full_detail: str) -> None:
    """Persist full DAG execution result to WORKING tier (Redis) with 1-hour TTL.

    Key format: dag_result:<session_id>
    TTL: 3600 seconds (1 hour)
    This enables supervisor to independently validate DAG output without trusting verbal reports.
    """
    key = f"dag_result:{session_id}"
    now = time.time()
    record: RecordDict = {
        "id": key,
        "agent_role": _ROLE,
        "key": key,
        "content": full_detail,
        "metadata": {"type": "dag_result", "session_id": session_id},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "created_at_ts": now,
        "expires_at": now + 3600,
    }
    # Direct backend write with TTL enforcement
    await mm.working.backend.set(_ROLE, key, record, expires_at=record["expires_at"])


async def retrieve_dag_result(mm: MemoryManager, session_id: str) -> str | None:
    """Retrieve full DAG result from WORKING tier (Redis) by session_id.

    Returns None if key is missing or expired.
    Supervisor uses this to independently validate DAG execution.
    """
    key = f"dag_result:{session_id}"
    record: RecordDict | None = await mm.working.backend.get(_ROLE, key)
    if record is None:
        return None
    return record.get("content")
