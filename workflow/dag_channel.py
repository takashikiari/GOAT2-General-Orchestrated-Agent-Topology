"""workflow.dag_channel — Redis-backed communication channel for a single DAG run.

Key schema (all keys get ``dag_ttl_seconds`` TTL):

    ``{prefix}:{dag_id}:status``   JSON dict  — current DAG state
    ``{prefix}:{dag_id}:outbox``   Redis list — DAG → orchestrator messages
    ``{prefix}:{dag_id}:inbox``    Redis list — orchestrator → DAG messages
    ``{prefix}:{dag_id}:result``   JSON dict  — final output on completion

The orchestrator reads ``outbox`` passively on each relevant turn; it writes
to ``inbox`` to send instructions mid-run.  No polling daemon required.
"""
from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as aioredis


class DagChannel:
    """Per-DAG Redis communication channel.

    No singleton — one instance per active DAG, owned by ``DagManager``.

    Args:
        redis_url: Connection string (e.g. ``redis://localhost:6379/0``).
        dag_id: Unique identifier for this DAG run.
        prefix: Key namespace prefix (default ``"dag"``).
        ttl: TTL in seconds applied to all keys (default 3600).
    """

    def __init__(
        self,
        redis_url: str,
        dag_id: str,
        *,
        prefix: str = "dag",
        ttl: int = 3600,
    ) -> None:
        self._url = redis_url
        self._dag_id = dag_id
        self._prefix = prefix
        self._ttl = ttl
        self._client: aioredis.Redis | None = None

    # ── key helpers ───────────────────────────────────────────────────────────

    @property
    def dag_id(self) -> str:
        return self._dag_id

    def _k(self, suffix: str) -> str:
        return f"{self._prefix}:{self._dag_id}:{suffix}"

    def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self._url, decode_responses=True)
        return self._client

    # ── status ────────────────────────────────────────────────────────────────

    async def set_status(self, state: str, node_states: dict[str, Any] | None = None) -> None:
        """Write DAG status.  ``state`` is one of: pending/running/done/failed/cancelled."""
        payload = {
            "state": state,
            "dag_id": self._dag_id,
            "updated_at": time.time(),
            "node_states": node_states or {},
        }
        await self._get_client().setex(self._k("status"), self._ttl, json.dumps(payload))

    async def get_status(self) -> dict[str, Any] | None:
        """Return the current DAG status dict, or ``None`` if not found."""
        raw = await self._get_client().get(self._k("status"))
        return json.loads(raw) if raw else None

    # ── result ────────────────────────────────────────────────────────────────

    async def set_result(self, results: dict[str, Any], errors: dict[str, str]) -> None:
        """Persist the final DAG output on completion or failure."""
        payload = {
            "dag_id": self._dag_id,
            "results": results,
            "errors": errors,
            "completed_at": time.time(),
        }
        await self._get_client().setex(self._k("result"), self._ttl, json.dumps(payload))

    async def get_result(self) -> dict[str, Any] | None:
        """Return the final result dict, or ``None`` if not yet complete."""
        raw = await self._get_client().get(self._k("result"))
        return json.loads(raw) if raw else None

    # ── outbox (DAG → orchestrator) ───────────────────────────────────────────

    async def push_outbox(self, message: str) -> None:
        """Append a message from the DAG to the orchestrator-facing outbox."""
        client = self._get_client()
        await client.lpush(self._k("outbox"), message)
        await client.expire(self._k("outbox"), self._ttl)

    async def read_outbox(self, limit: int = 20) -> list[str]:
        """Return up to ``limit`` outbox messages (newest first, non-destructive)."""
        items = await self._get_client().lrange(self._k("outbox"), 0, limit - 1)
        return list(items)

    # ── inbox (orchestrator → DAG) ────────────────────────────────────────────

    async def push_inbox(self, message: str) -> None:
        """Send a message from the orchestrator into the DAG's inbox."""
        client = self._get_client()
        await client.lpush(self._k("inbox"), message)
        await client.expire(self._k("inbox"), self._ttl)

    async def pop_inbox(self, timeout: float = 0.0) -> str | None:
        """Non-blocking pop of the oldest inbox message (``None`` if empty)."""
        result = await self._get_client().rpop(self._k("inbox"))
        return result if result else None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
