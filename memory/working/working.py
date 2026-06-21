"""
memory.working.working — session-scoped conversation memory backed by Redis.

Keys: ``goat2:working:{chat_id}``.  Non-list or corrupt values are logged as
WARNING and treated as empty — malformed Redis entries never crash the daemon.
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
    Redis client is built lazily; one instance shared across sessions.
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
        """Return messages for chat_id sorted by timestamp asc, or [] on any error."""
        data = await self._get_client().get(f"{_KEY_PREFIX}:{chat_id}")
        if not data:
            return []
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            log.warning("WorkingMemory: corrupt JSON for chat=%s, returning []", chat_id)
            return []
        if not isinstance(parsed, list):
            log.warning("WorkingMemory: expected list for chat=%s, got %s, returning []",
                        chat_id, type(parsed).__name__)
            return []
        return sorted(parsed, key=lambda m: float(m.get("timestamp", 0)))

    async def save_messages(self, chat_id: str, messages: list[dict]) -> None:
        """Persist the message list. Entries missing 'timestamp' are stamped now."""
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
        """Return chat_ids with valid list-valued entries (MGET, one round-trip).
        Corrupt or non-list keys are logged as WARNING and excluded."""
        keys = await self._get_client().keys(f"{_KEY_PREFIX}:*")
        if not keys:
            return []
        prefix_len = len(_KEY_PREFIX) + 1
        result = []
        for key, data in zip(keys, await self._get_client().mget(*keys)):
            chat_id = key[prefix_len:]
            try:
                parsed = json.loads(data) if data else []
            except json.JSONDecodeError:
                log.warning("WorkingMemory: corrupt JSON at key=%s, skipping", key)
                continue
            if not isinstance(parsed, list):
                log.warning("WorkingMemory: non-list value at key=%s (type=%s), skipping",
                            key, type(parsed).__name__)
                continue
            result.append(chat_id)
        return result
