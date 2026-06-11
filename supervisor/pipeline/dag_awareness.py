"""DAG awareness — GOAT reads working memory to see in-flight DAG sessions.

GOAT is the master of all three memory tiers. The DAG agents write
their progress to working memory (Redis) under the `dag:*` namespace.
This module gives GOAT the read-side primitives it needs to:

  1. Discover active DAG sessions before classifying a new intent.
  2. Read the current progress of a specific DAG on demand.
  3. Honor explicit user overrides (force CONVERSATIONAL / COMPLEX)
     for the duration of the session.

There is no DAG-execution logic here — only the read primitives
that let GOAT reason about what's in flight.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.pipeline")

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

__all__ = [
    "scan_active_dags",
    "read_dag_progress",
    "read_override",
    "write_override",
    "persist_session_override",
]


async def scan_active_dags(registry: "ServiceRegistry") -> list[dict]:
    """Read working memory for in-flight DAG sessions.

    Scans Redis for keys matching `dag:*:progress` and returns the
    decoded payloads. Used by the classifier so the LLM can see
    what is already running and prefer CONVERSATIONAL for
    follow-up questions about in-flight work.

    Args:
        registry: The ServiceRegistry holding the memory manager.

    Returns:
        A list of dicts: {session_id, wave, total_waves, status}.
        Empty list on any backend error.
    """
    mm = getattr(registry, "memory_manager", None)
    if mm is None:
        return []
    try:
        from config.roles import SESSION_ROLE
        try:
            records = await mm.working.backend.scan(SESSION_ROLE, "dag:*:progress")
        except AttributeError:
            return []
        active: list[dict] = []
        for rec in records[:5]:
            content = rec.get("content", "")
            if isinstance(content, str) and content:
                try:
                    payload = json.loads(content)
                    key = rec.get("key", "")
                    payload["session_id"] = key.split(":")[1] if key else "?"
                    active.append(payload)
                except Exception:
                    continue
        return active
    except Exception as e:
        log.debug("scan_active_dags failed: %s", e)
        return []


async def read_dag_progress(registry: "ServiceRegistry", session_id: str) -> dict | None:
    """Read the current progress record for a specific DAG session.

    Args:
        registry: The ServiceRegistry.
        session_id: The DAG session ID to inspect.

    Returns:
        Decoded progress dict (wave, total_waves, completed_tasks,
        status) or None if not found.
    """
    mm = getattr(registry, "memory_manager", None)
    if mm is None:
        return None
    try:
        from config.roles import SESSION_ROLE
        key = f"dag:{session_id}:progress"
        record = await mm.working.backend.get(SESSION_ROLE, key)
        if record is None:
            return None
        content = record.get("content")
        if not content:
            return None
        return json.loads(content) if isinstance(content, str) else None
    except Exception as e:
        log.debug("read_dag_progress failed: %s", e)
        return None


async def read_override(registry: "ServiceRegistry", session_id: str) -> str | None:
    """Read the user's current routing override for this session.

    Returns "conversational", "complex", or None.
    """
    mm = getattr(registry, "memory_manager", None)
    if mm is None:
        return None
    try:
        from config.roles import SESSION_ROLE
        key = f"goat:{session_id}:override"
        record = await mm.working.backend.get(SESSION_ROLE, key)
        if record is None:
            return None
        content = record.get("content")
        if not content:
            return None
        token = str(content).strip().lower()
        return token if token in ("conversational", "complex") else None
    except Exception as e:
        log.debug("read_override failed: %s", e)
        return None


async def write_override(registry: "ServiceRegistry", session_id: str, value: str) -> bool:
    """Persist the user's routing override for the rest of the session.

    Args:
        registry: The ServiceRegistry.
        session_id: The current GOAT session ID.
        value: "conversational" or "complex".

    Returns:
        True if write succeeded, False otherwise.
    """
    mm = getattr(registry, "memory_manager", None)
    if mm is None:
        return False
    try:
        from config.roles import SESSION_ROLE
        from config.limits import WORKING_MEMORY_TTL
        import time as _t
        key = f"goat:{session_id}:override"
        now = _t.time()
        record = {
            "id": key,
            "agent_role": SESSION_ROLE,
            "key": key,
            "content": value,
            "metadata": {"type": "goat_override", "session_id": session_id},
            "created_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now)),
            "created_at_ts": now,
            "expires_at": now + WORKING_MEMORY_TTL,
        }
        await mm.working.backend.set(
            SESSION_ROLE, key, record, expires_at=record["expires_at"]
        )
        log.info("override stored: session=%s value=%s", session_id, value)
        return True
    except Exception as e:
        log.debug("write_override failed: %s", e)
        return False


async def persist_session_override(
    registry: "ServiceRegistry",
    intent: str,
    session_id: str,
) -> None:
    """Detect (semantically) and persist the user's routing override.

    Convenience wrapper used by the supervisor on every turn: it
    asks the LLM whether the user explicitly requested a routing
    mode, and if so, stores the override in working memory so
    subsequent turns in the same session can apply it without
    re-asking the LLM.

    Best-effort: logs and continues on any error.
    """
    try:
        from supervisor.classification.classifier_context import detect_override
        override = await detect_override(intent, registry)
        if override:
            await write_override(registry, session_id, override)
    except Exception as e:
        log.debug("persist_session_override failed: %s", e)
