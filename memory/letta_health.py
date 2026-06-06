from __future__ import annotations

import logging
import time

import httpx

from config.settings import settings
from memory.letta_helpers import _HEALTH_CHECK_INTERVAL, _HTTP_TIMEOUT

log = logging.getLogger("goat2.memory.letta")


class LettaHealthProbe:
    """Manages the HTTP client and liveness state for the Letta REST API."""

    __slots__ = ("_cfg", "_http", "_available", "_last_health_check")

    def __init__(self) -> None:
        self._cfg                        = settings.letta
        self._http: httpx.AsyncClient | None = None
        self._available: bool | None         = None
        self._last_health_check: float       = 0.0

    async def get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._cfg.base_url,
                headers=self._cfg.headers,
                timeout=_HTTP_TIMEOUT,
            )
        return self._http

    async def check(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if (
            not force
            and self._available is not None
            and now - self._last_health_check < _HEALTH_CHECK_INTERVAL
        ):
            return self._available

        was_available = self._available
        try:
            http = await self.get_http()
            resp = await http.get("/v1/health/")
            self._available = resp.status_code == 200
        except Exception as exc:
            log.debug("Letta health probe failed: %s", exc)
            self._available = False

        self._last_health_check = now

        if was_available is None and not self._available:
            log.warning(
                "Letta unavailable at %s — using in-context fallback.",
                self._cfg.base_url,
            )
        elif was_available is False and self._available:
            log.info("Letta reconnected at %s", self._cfg.base_url)

        return self._available  # type: ignore[return-value]

    async def is_available(self) -> bool:
        return await self.check()

    def mark_unavailable(self) -> None:
        self._available         = False
        self._last_health_check = time.monotonic()

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None
