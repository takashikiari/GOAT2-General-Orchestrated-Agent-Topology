"""Session persistence: store conversation turns and DAG results to WORKING tier (Redis).

All turns (conversational and DAG) are stored to WORKING memory for cross-turn access.
This bridges DAG execution results into the conversational context layer.

MESSAGE SIZE MANAGEMENT:
=======================
Content stored to Redis is truncated to prevent oversized records:
- Structured turns: per-field caps (intent 500, summary 200, full_content 1000)
- DAG results: capped at _MAX_DAG_CHARS (50000) chars
- Truncation is logged for observability
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Final

log = logging.getLogger("goat2.supervisor.session")

from config.limits import DAG_RESULT_TTL, WORKING_MEMORY_TTL
from config.roles import SESSION_ROLE
from config.tiers import WORKING
from memory.working.working_record import RecordDict

if TYPE_CHECKING:
    from memory.shared import MemoryManager

__all__ = [
    "store_turn",
    "store_dag_result",
    "retrieve_dag_result",
    "write_dag_instructions",
    "retrieve_dag_instructions",
]

# Field truncation limits for structured turn records (per-field char caps).
_MAX_INTENT_CHARS: Final[int] = 500
_MAX_SUMMARY_CHARS: Final[int] = 200
_MAX_FULL_CONTENT_CHARS: Final[int] = 1000

# ── Size limits to prevent oversized Redis records ──
_MAX_DAG_CHARS: Final[int] = 50000     # max chars for a full DAG result

# TTL for DAG instructions written by GOAT before each pipeline run
DAG_INSTRUCTIONS_TTL: Final[int] = 3600


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
    log.warning(
        "%s truncated from %d to %d chars for storage",
        label, len(content), max_chars,
    )
    return content[:max_chars] + "\n\n[... truncated for storage ...]"


async def store_turn(
    mm: MemoryManager,
    turn: int,
    intent: str,
    summary: str,
    goat_action: str = "conversational_reply",
) -> None:
    """Persist one exchange to the WORKING tier as a structured JSON record.

    Replaces the old raw-text turn with structured data so GOAT can reason over
    prior exchanges. The JSON content carries:

        user_intent   — the user message, truncated to 500 chars
        goat_action   — conversational_reply | dag_spawn | clarification_request
        summary       — first 200 chars of the GOAT response
        full_content  — "User: …\\nGOAT: …", truncated to 1000 chars
        timestamp     — unix float
        turn_number   — the turn counter

    Stored under key ``turn_<int(timestamp)>_<turn_number>`` with TTL
    ``WORKING_MEMORY_TTL``. ``goat_action`` defaults to ``conversational_reply``;
    callers may pass ``dag_spawn`` or ``clarification_request`` when known.

    Args:
        mm: MemoryManager for tiered storage.
        turn: Turn counter (message count) for this exchange.
        intent: The user's message text.
        summary: The GOAT response text.
        goat_action: What GOAT did this turn (defaults to conversational_reply).
    """
    now = time.time()
    full_content = f"User: {intent}\nGOAT: {summary}"[:_MAX_FULL_CONTENT_CHARS]
    payload = json.dumps(
        {
            "user_intent": (intent or "")[:_MAX_INTENT_CHARS],
            "goat_action": goat_action,
            "summary": (summary or "")[:_MAX_SUMMARY_CHARS],
            "full_content": full_content,
            "timestamp": now,
            "turn_number": turn,
        },
        ensure_ascii=False,
    )
    key = f"turn_{int(now)}_{turn}"
    await mm.store(SESSION_ROLE, key, payload, memory_type=WORKING, ttl=WORKING_MEMORY_TTL)
    log.debug("store_turn: key=%s action=%s turn=%d", key, goat_action, turn)


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


async def write_dag_instructions(
    mm: "MemoryManager",
    session_id: str,
    intent: str,
    mem_ctx: str,
    capabilities: str,
) -> None:
    """Write GOAT-formulated task instructions for DAG to dag:<session_id>:instructions.

    GOAT calls this before _run_dag() so the DAG pipeline reads structured
    instructions instead of raw user intent. Key: dag:<session_id>:instructions
    TTL: DAG_INSTRUCTIONS_TTL (3600s).

    Args:
        mm: MemoryManager for Redis access.
        session_id: GOAT session ID (used as the DAG instruction target).
        intent: The user's original intent text.
        mem_ctx: Pre-computed memory context string.
        capabilities: String describing which DAG agent roles/tools are available.
    """
    payload = json.dumps({
        "intent": intent,
        "context": mem_ctx,
        "capabilities": capabilities,
        "constraints": "Use tools only. Never read from chat directly. Write results to working memory.",
        "session_id": session_id,
    }, ensure_ascii=False)
    key = f"dag:{session_id}:instructions"
    now = time.time()
    record: RecordDict = {
        "id": key,
        "agent_role": SESSION_ROLE,
        "key": key,
        "content": payload,
        "metadata": {"type": "dag_instructions", "session_id": session_id},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "created_at_ts": now,
        "expires_at": now + DAG_INSTRUCTIONS_TTL,
    }
    await mm.working.backend.set(SESSION_ROLE, key, record, expires_at=record["expires_at"])


async def retrieve_dag_instructions(
    mm: "MemoryManager",
    session_id: str,
) -> str | None:
    """Retrieve raw JSON instructions from dag:<session_id>:instructions.

    Args:
        mm: MemoryManager for Redis access.
        session_id: GOAT session ID to look up.

    Returns:
        Raw JSON content string, or None if key is missing/expired.
    """
    key = f"dag:{session_id}:instructions"
    record: RecordDict | None = await mm.working.backend.get(SESSION_ROLE, key)
    if record is None:
        return None
    return record.get("content")
