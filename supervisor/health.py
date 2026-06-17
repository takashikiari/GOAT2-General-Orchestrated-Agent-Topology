"""Service health probes — quick liveness check for Redis, ChromaDB, Letta, SearXNG.

Used by GOAT via the ``memory_health`` tool and by operators during
incident triage. Probes run in parallel with a per-service ceiling
(``HEALTH_TIMEOUT``); any timeout, exception, or HTTP error is
swallowed and reported as DOWN. ``health_check`` itself NEVER raises.

ARCHITECTURE:
=============
This is a pure function over the ``ServiceRegistry``. No module-level
state, no singletons — pass the registry you want to probe (the
canonical one comes from ``tools.registry_accessor.get_registry()``).

The probes reuse existing client methods:
    - Redis:      ``working_memory.backend.ping()``    (memory/working/redis_backend.py:83)
    - ChromaDB:   ``memory_manager.episodic.health()`` (memory/episodic/chroma_crud.py:204)
    - Letta:      ``letta_client.health()``            (memory/long_term/letta_client.py:489)
    - SearXNG:    HTTP GET on ``<SEARXNG_URL>/healthz``

Each client method already returns ``bool`` and swallows its own
internal errors. The ``asyncio.wait_for`` here is a defensive outer
ceiling so a hung client cannot pin the gather forever.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

import httpx

from config.limits import HEALTH_TIMEOUT, SEARXNG_URL, SEARXNG_TIMEOUT

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.supervisor.health")

__all__ = ["health_check"]

_DEFAULT_KEYS: tuple[str, ...] = ("redis", "chromadb", "letta", "searxng")


async def _probe_redis(registry: "ServiceRegistry", timeout: float) -> bool:
    """Probe Redis via ``working_memory.backend.ping()`` with a wait_for ceiling."""
    try:
        backend = registry.working_memory.backend
        return bool(await asyncio.wait_for(backend.ping(), timeout=timeout))
    except Exception as exc:  # noqa: BLE001 — health probe must never raise
        log.warning("health: redis probe failed: %s", exc)
        return False


async def _probe_chroma(registry: "ServiceRegistry", timeout: float) -> bool:
    """Probe ChromaDB via ``memory_manager.episodic.health()`` with a wait_for ceiling."""
    try:
        ok = await asyncio.wait_for(
            registry.memory_manager.episodic.health(), timeout=timeout,
        )
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        log.warning("health: chromadb probe failed: %s", exc)
        return False


async def _probe_letta(registry: "ServiceRegistry", timeout: float) -> bool:
    """Probe Letta via ``letta_client.health()`` with a wait_for ceiling."""
    try:
        ok = await asyncio.wait_for(
            registry.letta_client.health(), timeout=timeout,
        )
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        log.warning("health: letta probe failed: %s", exc)
        return False


async def _probe_searxng(timeout: float) -> bool:
    """Probe SearXNG via HTTP GET on ``<url>/healthz`` (falls back to root)."""
    base = SEARXNG_URL.rstrip("/")
    # Prefer /healthz (most SearXNG instances expose this), fall back to /.
    for path in ("/healthz", "/"):
        try:
            async with httpx.AsyncClient(
                timeout=min(timeout, float(SEARXNG_TIMEOUT)),
            ) as client:
                resp = await client.get(f"{base}{path}")
                if resp.status_code < 500:
                    return True
                log.warning("health: searxng %s → HTTP %d", path, resp.status_code)
        except Exception as exc:  # noqa: BLE001
            log.warning("health: searxng %s failed: %s", path, exc)
            continue
    return False


async def health_check(
    registry: "ServiceRegistry",
    *,
    timeout_s: float | None = None,
) -> dict[str, bool]:
    """Probe all four services in parallel; return a status dict.

    Returns:
        ``{"redis": bool, "chromadb": bool, "letta": bool, "searxng": bool,
           "overall": bool}`` where ``overall`` is True iff every probe
        returned True.

    Raises:
        Never. Any error inside a probe is logged at WARNING and the
        corresponding field is False.

    Args:
        registry: The ``ServiceRegistry`` to probe. Use
            ``tools.registry_accessor.get_registry()`` to obtain the
            canonical application-level registry.
        timeout_s: Optional override for the per-service ceiling.
            Defaults to ``config.limits.HEALTH_TIMEOUT`` (5s).
    """
    timeout = timeout_s if timeout_s is not None else HEALTH_TIMEOUT

    redis_t    = asyncio.create_task(_probe_redis(registry, timeout),   name="probe_redis")
    chroma_t   = asyncio.create_task(_probe_chroma(registry, timeout),  name="probe_chroma")
    letta_t    = asyncio.create_task(_probe_letta(registry, timeout),   name="probe_letta")
    searxng_t  = asyncio.create_task(_probe_searxng(timeout),          name="probe_searxng")

    # Outer wait_for on the gather is purely defensive — every probe
    # already has its own internal timeout, but a hung task could
    # otherwise pin us. return_exceptions=True so a stray bug in a
    # probe task doesn't propagate as a raise.
    try:
        results = await asyncio.wait_for(
            asyncio.gather(redis_t, chroma_t, letta_t, searxng_t, return_exceptions=True),
            timeout=timeout * 2 + 1.0,
        )
    except asyncio.TimeoutError:
        log.error("health: outer gather timed out — treating all as DOWN")
        for t in (redis_t, chroma_t, letta_t, searxng_t):
            if not t.done():
                t.cancel()
        return {k: False for k in _DEFAULT_KEYS} | {"overall": False}

    # Coerce stray exceptions to False defensively.
    coerced: list[bool] = [r if isinstance(r, bool) else False for r in results]
    redis_ok, chroma_ok, letta_ok, searxng_ok = coerced
    report: dict[str, Any] = {
        "redis":    redis_ok,
        "chromadb": chroma_ok,
        "letta":    letta_ok,
        "searxng":  searxng_ok,
    }
    report["overall"] = all(report[k] for k in _DEFAULT_KEYS)
    log.info("health: %s", report)
    return report
