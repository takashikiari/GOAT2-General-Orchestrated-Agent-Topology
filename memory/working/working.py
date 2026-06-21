"""
memory.working.working — session-scoped conversation memory backed by Redis.

WorkingMemory IS the Redis implementation — no abstract backend layer.
If the storage technology changes, this file changes.  No indirection for
its own sake.

Keys live under the namespace ``goat2:working:{chat_id}``.  TTL is applied
per-key when WORKING_TTL_SECONDS > 0; otherwise keys persist until evicted
or the Redis instance is cleared.

Usage:
    wm = WorkingMemory()
    msgs = await wm.get_messages("123")
    await wm.save_messages("123", msgs)
"""
from __future__ import annotations

import json

from memory.config import WORKING_STORAGE_URL, WORKING_TTL_SECONDS
from utils.logging.setup import get_logger

log = get_logger(__name__)

_KEY_PREFIX = "goat2:working"


class WorkingMemory:
    """
    Session-scoped conversation memory, stored in Redis.

    The Redis client is built lazily on first use — importing this class
    never opens a network connection.  One WorkingMemory instance is shared
    across all chat sessions (via the ServiceRegistry); the Redis client
    handles concurrent access safely.
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

    async def get_messages(self, chat_id: str) -> list[dict[str, str]]:
        """
        Return the stored message list for chat_id, or [] if none exists.

        Args:
            chat_id: Unique identifier for the conversation session.

        Returns:
            List of {"role": ..., "content": ...} dicts, oldest first.
        """
        key = f"{_KEY_PREFIX}:{chat_id}"
        data = await self._get_client().get(key)
        if data is None:
            return []
        return json.loads(data)

    async def save_messages(
        self, chat_id: str, messages: list[dict[str, str]]
    ) -> None:
        """
        Persist the message list for chat_id.

        Applies TTL from memory.config if WORKING_TTL_SECONDS > 0.

        Args:
            chat_id:  Unique identifier for the conversation session.
            messages: Full message list to store (overwrites any prior value).
        """
        key = f"{_KEY_PREFIX}:{chat_id}"
        payload = json.dumps(messages)
        client = self._get_client()
        if WORKING_TTL_SECONDS > 0:
            await client.setex(key, WORKING_TTL_SECONDS, payload)
        else:
            await client.set(key, payload)
        log.debug("WorkingMemory: saved %d messages for chat=%s", len(messages), chat_id)
