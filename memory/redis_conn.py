from __future__ import annotations

import logging
from typing import Final

from memory.types import AgentRole, MemoryKey

log = logging.getLogger("goat2.memory.working")

_KEY_PREFIX: Final[str] = "goat2:working"


class RedisConn:
    """Manages the async Redis client connection, key formatting, and lifecycle."""

    __slots__ = ("_url", "_max_connections", "_socket_timeout",
                 "_decode_responses", "_redis")

    def __init__(
        self, url: str = "redis://localhost:6379/0",
        *, max_connections: int = 10, socket_timeout: float = 5.0,
        decode_responses: bool = True,
    ) -> None:
        self._url, self._max_connections   = url, max_connections
        self._socket_timeout               = socket_timeout
        self._decode_responses             = decode_responses
        self._redis: object | None         = None

    def _rkey(self, ns: AgentRole, key: MemoryKey) -> str:
        # Pure — PyO3 candidate: fn rkey(ns: &str, key: &str) -> String
        return f"{_KEY_PREFIX}:{ns}:{key}"

    def _ns_pattern(self, ns: AgentRole) -> str:
        # Pure — PyO3 candidate: fn ns_pattern(ns: &str) -> String
        return f"{_KEY_PREFIX}:{ns}:*"

    def _ns_prefix(self, ns: AgentRole) -> str:
        # Pure — PyO3 candidate: fn ns_prefix(ns: &str) -> String
        return f"{_KEY_PREFIX}:{ns}:"

    async def _get_redis(self) -> object:
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
            except ImportError as exc:
                raise RuntimeError(
                    "RedisBackend requires 'redis[hiredis]>=5.0'."
                ) from exc
            self._redis = aioredis.from_url(
                self._url, max_connections=self._max_connections,
                socket_timeout=self._socket_timeout,
                decode_responses=self._decode_responses,
            )
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()  # type: ignore[union-attr]
            self._redis = None
