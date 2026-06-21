"""
memory.working.working — session-scoped conversation memory backed by Redis.

WorkingMemory IS the Redis implementation — no abstract backend layer.
Keys live under ``goat2:working:{chat_id}``.  Each message dict is stored
with a "timestamp" field (Unix epoch float); save_messages stamps any
unstamped entries so older data is handled safely.
"""
from __future__ import annotations

import json
import time

from memory.config import WORKING_STORAGE_URL, WORKING_TTL_SECONDS
from utils.logging.setup import get_logger

log = get_logger(__name__)

_KEY_PREFIX = "goat2:working"


class WorkingMemory:
    """
    Session-scoped conversation memory, stored in Redis.

    The Redis client is built lazily on first use.  One instance is shared
    across all sessions via the ServiceRegistry.
    """

    def __init__(self) -> None:
        """Initialise with no connection — built on first use."""
        self._client = None

    def _get_client(self):
        """Return (and lazily create) the async Redis client."""
        if self._client is None:
            import redis.asyncio as aioredis  # lazy — avoids import-time connection
            self._client = aioredis.from_url(
                WORKING_STORAGE_URL, decode_responses=True
            )
            log.debug("WorkingMemory: Redis client created (%s)", WORKING_STORAGE_URL)
        return self._client

    async def get_messages(self, chat_id: str) -> list[dict]:
        """Return stored messages for chat_id sorted by timestamp ascending, or []."""
        data = await self._get_client().get(f"{_KEY_PREFIX}:{chat_id}")
        msgs = json.loads(data) if data else []
        return sorted(msgs, key=lambda m: float(m.get("timestamp", 0)))

    async def save_messages(self, chat_id: str, messages: list[dict]) -> None:
        """
        Persist the message list for chat_id.

        Entries missing a "timestamp" field are stamped with the current time
        so the promotion daemon can age them correctly.
        """
        now = time.time()
        stamped = [m if "timestamp" in m else {**m, "timestamp": now} for m in messages]
        key = f"{_KEY_PREFIX}:{chat_id}"
        payload = json.dumps(stamped)
        client = self._get_client()
        if WORKING_TTL_SECONDS > 0:
            await client.setex(key, WORKING_TTL_SECONDS, payload)
        else:
            await client.set(key, payload)
        log.debug("WorkingMemory: saved %d messages for chat=%s", len(stamped), chat_id)

    async def list_chat_ids(self) -> list[str]:
        """Return all chat_ids that currently have working memory entries."""
        keys = await self._get_client().keys(f"{_KEY_PREFIX}:*")
        prefix_len = len(_KEY_PREFIX) + 1  # +1 for the ':'
        return [k[prefix_len:] for k in keys]
