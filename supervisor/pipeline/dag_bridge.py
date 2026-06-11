"""DagBridge — Redis polling communication between GOAT supervisor and DAG agents.

Provides non-blocking polling for DAG result retrieval. GOAT subscribes to
dag:<session_id>:result via Redis polling (not blocking). Every 0.5s checks
if dag:<session_id>:result exists. When found → notifies GOAT Validator.

KEY FORMAT:
===========
- Result key: dag:{session_id}:result
- Progress key: dag:{session_id}:progress (optional, non-blocking)
- TTL: Configured in config.limits.DAG_RESULT_TTL

TOOL DISTRIBUTION:
=================
- DAG agents: FILE_TOOLS + WEB_SEARCH + DAG_MEMORY_TOOLS (dag:* namespace)
- GOAT CONVERSATIONAL: FILE_TOOLS + MEMORY_TOOLS (all tiers, goat:* namespace)
- GOAT VALIDATOR: direct memory_manager access only, no tool calls
- GOAT Memory Promoter: direct memory_manager.promote() only

This module is distinct from session.py because:
- session.py: Stores/retrieves full DAG execution detail
- dag_bridge.py: Polls for result presence with timeout semantics
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from config.limits import DAG_RESULT_TTL
from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.pipeline")

__all__ = ["DagBridge", "DAG_KEY_PREFIX", "DAG_NAMESPACE"]

# Redis key prefixes for DAG communication
DAG_KEY_PREFIX: Final[str] = "dag"
DAG_NAMESPACE: Final[str] = "dag"  # Redis key namespace for DAG agents

# Polling configuration
POLL_INTERVAL: Final[float] = 0.5  # seconds between polls
DEFAULT_TIMEOUT: Final[float] = 120.0  # default timeout in seconds


class DagBridge:
    """Redis polling bridge for DAG result retrieval.

    Provides non-blocking poll semantics for waiting on DAG execution
    results. GOAT uses this to wait for DAG completion without blocking
    the event loop.

    TOOL DISTRIBUTION:
    ==================
    - DAG agents: FILE_TOOLS + WEB_SEARCH + DAG_MEMORY_TOOLS (dag:* namespace)
    - GOAT CONVERSATIONAL: FILE_TOOLS + MEMORY_TOOLS (all tiers, goat:* namespace)
    - GOAT VALIDATOR: direct memory_manager access only, no tool calls
    - GOAT Memory Promoter: direct memory_manager.promote() only

    Example:
        bridge = DagBridge(memory_manager)
        result = await bridge.wait_for_result(session_id, timeout=120)
        if result:
            # Process DAG result
            pass
    """

    def __init__(
        self,
        memory_manager: "MemoryManager",
        poll_interval: float = POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize DagBridge with memory manager.

        Args:
            memory_manager: MemoryManager for Redis access.
            poll_interval: Seconds between polls (default 0.5s).
            timeout: Default timeout in seconds (default 120s).
        """
        self._mm = memory_manager
        self._poll_interval = poll_interval
        self._default_timeout = timeout

    def _result_key(self, session_id: str) -> str:
        """Generate Redis key for DAG result.

        Args:
            session_id: The session identifier.

        Returns:
            Redis key in format: dag:{session_id}:result
        """
        return f"{DAG_KEY_PREFIX}:{session_id}:result"

    def _progress_key(self, session_id: str) -> str:
        """Generate Redis key for DAG progress (optional).

        Args:
            session_id: The session identifier.

        Returns:
            Redis key in format: dag:{session_id}:progress
        """
        return f"{DAG_KEY_PREFIX}:{session_id}:progress"

    async def wait_for_result(
        self,
        session_id: str,
        timeout: float | None = None,
    ) -> str | None:
        """Poll for DAG result with timeout.

        Polls Redis every 0.5s checking for dag:{session_id}:result.
        Returns immediately when found, or None on timeout.

        Args:
            session_id: The session identifier to wait for.
            timeout: Timeout in seconds (default from constructor).

        Returns:
            Result content as string, or None if not found within timeout.
        """
        timeout = timeout or self._default_timeout
        key = self._result_key(session_id)
        max_attempts = int(timeout / self._poll_interval)

        log.info(
            "DagBridge: waiting for result key=%s timeout=%.1fs attempts=%d",
            key, timeout, max_attempts,
        )

        for attempt in range(max_attempts):
            # Check if result exists
            try:
                from memory.working.working_record import RecordDict

                record: RecordDict | None = await self._mm.working.backend.get(
                    SESSION_ROLE, key
                )
                if record is not None:
                    content = record.get("content")
                    if content:
                        log.info(
                            "DagBridge: found result for session=%s attempt=%d",
                            session_id, attempt + 1,
                        )
                        return content
            except Exception as e:
                log.debug("DagBridge: poll attempt %d failed: %s", attempt + 1, e)

            # Wait before next poll
            await asyncio.sleep(self._poll_interval)

        log.warning(
            "DagBridge: timeout waiting for session=%s after %d attempts",
            session_id, max_attempts,
        )
        return None

    async def get_result_if_ready(
        self,
        session_id: str,
    ) -> str | None:
        """Get DAG result if available, without polling.

        Non-blocking check for result presence. Returns immediately
        whether found or not.

        Args:
            session_id: The session identifier.

        Returns:
            Result content if available, None otherwise.
        """
        key = self._result_key(session_id)
        try:
            from memory.working.working_record import RecordDict

            record: RecordDict | None = await self._mm.working.backend.get(
                SESSION_ROLE, key
            )
            if record is not None:
                return record.get("content")
        except Exception as e:
            log.debug("DagBridge: get_result_if_ready failed: %s", e)
        return None

    async def get_progress(
        self,
        session_id: str,
    ) -> str | None:
        """Get optional progress update from DAG.

        DAG may write progress to dag:{session_id}:progress (optional).
        GOAT can read progress updates without blocking execution.

        Args:
            session_id: The session identifier.

        Returns:
            Progress content if available, None otherwise.
        """
        key = self._progress_key(session_id)
        try:
            from memory.working.working_record import RecordDict

            record: RecordDict | None = await self._mm.working.backend.get(
                SESSION_ROLE, key
            )
            if record is not None:
                return record.get("content")
        except Exception as e:
            log.debug("DagBridge: get_progress failed: %s", e)
        return None

    async def write_result(
        self,
        session_id: str,
        content: str,
    ) -> bool:
        """Write DAG result to Redis (for DAG agent use).

        Args:
            session_id: The session identifier.
            content: Result content to store.

        Returns:
            True if write succeeded, False otherwise.
        """
        import time

        key = self._result_key(session_id)
        now = time.time()

        try:
            from memory.working.working_record import RecordDict

            record: RecordDict = {
                "id": key,
                "agent_role": SESSION_ROLE,
                "key": key,
                "content": content,
                "metadata": {"type": "dag_result", "session_id": session_id},
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "created_at_ts": now,
                "expires_at": now + DAG_RESULT_TTL,
            }
            await self._mm.working.backend.set(
                SESSION_ROLE, key, record, expires_at=record["expires_at"]
            )
            log.info("DagBridge: wrote result for session=%s", session_id)
            return True
        except Exception as e:
            log.error("DagBridge: write_result failed: %s", e)
            return False

    async def write_progress(
        self,
        session_id: str,
        progress: str,
    ) -> bool:
        """Write progress update to Redis (optional, non-blocking).

        DAG can write progress updates without blocking execution.
        This is optional - GOAT checks for progress but doesn't wait.

        Args:
            session_id: The session identifier.
            progress: Progress content to store.

        Returns:
            True if write succeeded, False otherwise.
        """
        import time

        key = self._progress_key(session_id)
        now = time.time()

        try:
            from memory.working.working_record import RecordDict

            record: RecordDict = {
                "id": key,
                "agent_role": SESSION_ROLE,
                "key": key,
                "content": progress,
                "metadata": {"type": "dag_progress", "session_id": session_id},
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "created_at_ts": now,
                "expires_at": now + DAG_RESULT_TTL,
            }
            await self._mm.working.backend.set(
                SESSION_ROLE, key, record, expires_at=record["expires_at"]
            )
            log.debug("DagBridge: wrote progress for session=%s", session_id)
            return True
        except Exception as e:
            log.debug("DagBridge: write_progress failed: %s", e)
            return False