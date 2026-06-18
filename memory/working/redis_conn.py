from __future__ import annotations

import logging
import os
from typing import Final

from memory.shared.types import AgentRole, MemoryKey

log = logging.getLogger("goat2.memory.working")

_KEY_PREFIX: Final[str] = "goat2:working"

# Defaults — overridden by config/memory.toml [redis] at import time.
# Env vars take precedence over the toml file.
_REDIS_DEFAULTS: Final[dict[str, object]] = {
    "url":             "redis://localhost:6379/0",
    "max_connections": 10,
    "socket_timeout":  5.0,
}


def _load_redis_config() -> dict[str, object]:
    """Read Redis connection settings from config/memory.toml [redis].

    Resolution order: env var > toml > module default. The toml loader
    is non-fatal — a missing or unparseable file silently falls back to
    defaults, so the working tier stays usable in any environment.

    Returns:
        dict with keys ``url`` (str), ``max_connections`` (int),
        ``socket_timeout`` (float). Values are the post-resolution
        settings, never the raw toml payload.
    """
    cfg: dict[str, object] = dict(_REDIS_DEFAULTS)
    try:
        from config.modular_loader import load_memory_config
        toml_cfg = load_memory_config()
        redis_section = toml_cfg.get("redis", {}) or {}
        for key in ("url", "max_connections", "socket_timeout"):
            if key in redis_section and redis_section[key] is not None:
                cfg[key] = redis_section[key]
    except Exception as exc:
        log.debug("redis_conn: memory.toml [redis] load skipped: %s", exc)
    # Env-var override.
    if os.environ.get("REDIS_URL"):
        cfg["url"] = os.environ["REDIS_URL"]
    if os.environ.get("REDIS_MAX_CONNECTIONS"):
        try:
            cfg["max_connections"] = int(os.environ["REDIS_MAX_CONNECTIONS"])
        except ValueError:
            log.warning(
                "redis_conn: REDIS_MAX_CONNECTIONS=%r not an int — using default",
                os.environ["REDIS_MAX_CONNECTIONS"],
            )
    if os.environ.get("REDIS_SOCKET_TIMEOUT"):
        try:
            cfg["socket_timeout"] = float(os.environ["REDIS_SOCKET_TIMEOUT"])
        except ValueError:
            log.warning(
                "redis_conn: REDIS_SOCKET_TIMEOUT=%r not a float — using default",
                os.environ["REDIS_SOCKET_TIMEOUT"],
            )
    return cfg


_REDIS_CONFIG: Final[dict[str, object]] = _load_redis_config()


class RedisConn:
    """Manages the async Redis client connection, key formatting, and lifecycle."""

    __slots__ = ("_url", "_max_connections", "_socket_timeout",
                 "_decode_responses", "_redis")

    def __init__(
        self, url: str | None = None,
        *, max_connections: int | None = None, socket_timeout: float | None = None,
        decode_responses: bool = True,
    ) -> None:
        # Explicit kwargs win over config. Config wins over the hardcoded default.
        self._url              = url             if url             is not None else str(_REDIS_CONFIG["url"])
        self._max_connections  = max_connections if max_connections is not None else int(_REDIS_CONFIG["max_connections"])
        self._socket_timeout   = socket_timeout  if socket_timeout  is not None else float(_REDIS_CONFIG["socket_timeout"])
        self._decode_responses = decode_responses
        self._redis: object | None         = None
        log.debug(
            "RedisConn: initialised (url=%s max=%d timeout=%.1fs)",
            self._url, self._max_connections, self._socket_timeout,
        )

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
            log.debug("RedisConn._get_redis: client opened")
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()  # type: ignore[union-attr]
            self._redis = None
            log.debug("RedisConn.close: client closed")
