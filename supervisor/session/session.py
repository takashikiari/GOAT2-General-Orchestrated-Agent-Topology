"""Session persistence: store conversation turns and DAG results to WORKING tier (Redis).

All turns (conversational and DAG) are stored to WORKING memory for cross-turn access.
This bridges DAG execution results into the conversational context layer.

MESSAGE SIZE MANAGEMENT:
=======================
Content stored to Redis is truncated to prevent oversized records:
- Turn summaries: capped at _MAX_TURN_CHARS (10000) chars
- DAG results: capped at _MAX_DAG_CHARS (50000) chars
- Truncation is logged for observability
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from config.limits import DAG_RESULT_TTL
from config.roles import SESSION_ROLE
from config.tiers import WORKING
from memory.working.working_record import RecordDict

if TYPE_CHECKING:
    from memory.shared import MemoryManager

__all__ = ["store_turn", "store_dag_result", "retrieve_dag_result"]

# ── Size limits to prevent oversized Redis records ──
_MAX_TURN_CHARS: Final[int] = 10000    # max chars for a single turn summary
_MAX_DAG_CHARS: Final[int] = 50000     # max chars for a full DAG result


def _truncate_for_storage(content: str, max_chars: int, label: str) -> str:
    """Truncate content to max_chars for storage, with a notice.

    Args:
        content: The string to truncate.
        max_chars: Maximum allowed character count.
        label: Descriptive label for logging (e.g., 'turn', 'dag_result').

    Returns:
        Truncated string, or original if within limit.
    """
    if not content or len(content) <= max_chars:
        return content
    import logging
    log = logging.getLogger("goat2.session")
    log.warning(
        "%s truncated from %d to %d chars for storage",
        label, len(content), max_chars,
    )
    return content[:max_chars] + "\n\n[... truncated for storage ...]"


async def store_turn(mm: MemoryManager, turn: int, intent: str, summary: str) -> None:
    """Persist one exchange to WORKING tier (Redis) only.

    Both conversational responses and DAG results are stored here. This enables
    the conversational path to access prior DAG execution results via memory tools.
    GOAT supervisor handles promotion to EPISODIC/LONG_TERM at session end.

    Content is truncated to _MAX_TURN_CHARS to prevent oversized Redis records.
    """
    content = f"User: {intent}\nAssistant: {summary}"
    content = _truncate_for_storage(content, _MAX_TURN_CHARS, "turn")
    await mm.store(SESSION_ROLE, f"turn_{turn:04d}", content, memory_type=WORKING)


async def store_dag_result(mm: MemoryManager, session_id: str, full_detail: str) -> None:
    """Persist full DAG execution result to WORKING tier (Redis) with configurable TTL.

    Key format: dag_result:<session_id>
    TTL: DAG_RESULT_TTL from config.limits (default 3600 seconds / 1 hour)
    This enables supervisor to independently validate DAG output without trusting verbal reports.

    Content is truncated to _MAX_DAG_CHARS to prevent oversized Redis records.
    """
    full_detail = _truncate_for_storage(full_detail, _MAX_DAG_CHARS, "dag_result")
    key = f"dag_result:{session_id}"
    now = time.time()
    record: RecordDict = {
        "id": key,
        "agent_role": SESSION_ROLE,
        "key": key,
        "content": full_detail,
        "metadata": {"type": "dag_result", "session_id": session_id},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "created_at_ts": now,
        "expires_at": now + DAG_RESULT_TTL,
    }
    # Direct backend write with TTL enforcement
    await mm.working.backend.set(SESSION_ROLE, key, record, expires_at=record["expires_at"])


async def retrieve_dag_result(mm: MemoryManager, session_id: str) -> str | None:
    """Retrieve full DAG result from WORKING tier (Redis) by session_id.

    Returns None if key is missing or expired.
    Supervisor uses this to independently validate DAG execution.
    """
    key = f"dag_result:{session_id}"
    record: RecordDict | None = await mm.working.backend.get(SESSION_ROLE, key)
    if record is None:
        return None
    return record.get("content")
