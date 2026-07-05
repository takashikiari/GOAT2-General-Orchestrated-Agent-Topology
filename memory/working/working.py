"""
memory.working.working — session-scoped conversation memory backed by Redis.

Keys: ``goat2:working:{chat_id}``.  Non-list or corrupt values are logged as
WARNING and treated as empty — malformed Redis entries never crash the daemon.

Each chat_id has its own ``asyncio.Lock`` that serialises all read-modify-write
cycles on the same key.  Callers that need an atomic multi-step sequence (e.g.
read → append → save, or read → promote → save) must hold ``chat_lock(chat_id)``
across the entire sequence and call the ``_raw`` variants inside.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict

import redis.asyncio as aioredis

from memory.config import WORKING_STORAGE_URL, WORKING_TTL_SECONDS
from utils.logging.setup import get_logger

log = get_logger(__name__)

_KEY_PREFIX = "goat2:working"


class WorkingMemory:
    """Session-scoped conversation memory, stored in Redis."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(WORKING_STORAGE_URL, decode_responses=True)
            log.debug("WorkingMemory: Redis client created (%s)", WORKING_STORAGE_URL)
        return self._client

    def chat_lock(self, chat_id: str) -> asyncio.Lock:
        """Per-chat lock for atomic multi-step sequences."""
        return self._locks[chat_id]

    # --- raw (no-lock) variants: callers must hold chat_lock ----------------

    async def get_messages_raw(self, chat_id: str) -> list[dict]:
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

    async def save_messages_raw(self, chat_id: str, messages: list[dict]) -> None:
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

    # --- locked public API --------------------------------------------------

    async def get_messages(self, chat_id: str) -> list[dict]:
        """Return messages sorted by timestamp asc, or [] on any error."""
        async with self._locks[chat_id]:
            return await self.get_messages_raw(chat_id)

    async def save_messages(self, chat_id: str, messages: list[dict]) -> None:
        """Persist the message list under the per-chat lock."""
        async with self._locks[chat_id]:
            await self.save_messages_raw(chat_id, messages)

    async def list_chat_ids(self) -> list[str]:
        """Return chat_ids with valid list-valued entries (MGET, one round-trip)."""
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
