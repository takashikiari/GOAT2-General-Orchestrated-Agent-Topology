"""Last-write timestamp synchronisation — single canonical implementation.

Every write to a memory tier (working, episodic, long-term) should record
its timestamp in Redis under ``goat2:working:last_write:<tier>`` so the
``MEMORY_LAST_WRITE`` tool can surface "last write" information without
scanning the tier.

The single async function ``sync_last_write`` is the only place that touches
Redis for this purpose. It accepts the working backend (which is owned
by the registry's ``MemoryManager``) and a tier name. Callers must
NEVER instantiate a fresh ``RedisBackend`` for last-write writes —
doing so creates a parallel connection pool and bypasses the registry.

Fallback behaviour:
  - If no ``working_backend`` is provided and the registry is reachable,
    the function uses the registry's ``memory_manager.working.backend``.
  - If neither is available, the write is skipped silently (this is a
    best-effort diagnostic, never a hard requirement).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.last_write")

_KEY_PREFIX = "goat2:working:last_write"


def _resolve_registry_working_backend() -> "WorkingMemoryBackend | None":
    """Return the registry-owned working backend, or ``None`` if not initialised.

    Used as a fallback when a caller doesn't have a backend passed in.
    Returns ``None`` (silent) when the registry isn't reachable so that
    last-write tracking degrades gracefully outside the normal app boot
    path (tests, ad-hoc scripts, etc.).
    """
    try:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        mm = getattr(registry, "memory_manager", None)
        if mm is None:
            return None
        return mm.working.backend
    except RuntimeError:
        # Registry not initialised — silent fallback for tests / ad-hoc.
        return None
    except Exception as exc:
        log.debug("_resolve_registry_working_backend failed: %s", exc)
        return None


async def sync_last_write(
    tier: str,
    *,
    working_backend: "WorkingMemoryBackend | None" = None,
    iso_format: bool = False,
) -> bool:
    """Record the current timestamp as the last write to ``tier``.

    Uses the registry-owned working backend when available; falls back
    to the caller-supplied backend. Never raises — last-write tracking
    is best-effort diagnostic metadata.

    Args:
        tier: Tier name (``"working"``, ``"episodic"``, ``"long_term"``).
        working_backend: The working-tier backend owned by the registry's
            ``MemoryManager``. When ``None`` the function attempts a
            registry lookup; if that also fails the write is skipped.
        iso_format: When ``True`` store an ISO 8601 string; when ``False``
            store a Unix float (str).

    Returns:
        ``True`` if the write succeeded, ``False`` otherwise.
    """
    backend = working_backend or _resolve_registry_working_backend()
    if backend is None:
        log.debug(
            "sync_last_write: no working backend available — skipping tier=%s",
            tier,
        )
        return False
    try:
        ts = (
            datetime.now(timezone.utc).isoformat()
            if iso_format
            else time.time()
        )
        key = f"{_KEY_PREFIX}:{tier}"
        # backend.set signature: (agent_role, key, value: dict, expires_at).
        # Wrap the timestamp in a dict so the backend's JSON layer treats
        # it as a record; reader (read_last_write) unwraps the same way.
        # Pass None for expires_at so the diagnostic marker is permanent
        # (until the next deploy that purges that namespace).
        await backend.set(tier, key, {"value": str(ts), "tier": tier}, None)
        return True
    except Exception as exc:
        log.debug(
            "sync_last_write: tier=%s write failed (non-blocking): %s",
            tier, exc,
        )
        return False


async def read_last_write(
    tier: str,
    *,
    working_backend: "WorkingMemoryBackend | None" = None,
) -> str | None:
    """Read the last-write timestamp for ``tier``, or ``None`` if not set.

    Same backend resolution as :func:`sync_last_write`. The returned
    string is the stored timestamp value (ISO 8601 or Unix float) —
    callers that care about format should parse it themselves.

    Args:
        tier: Tier name (``"working"``, ``"episodic"``, ``"long_term"``).
        working_backend: Optional explicit working backend; when ``None``
            the registry is consulted.

    Returns:
        The stored timestamp string, or ``None`` if absent / unavailable.
    """
    backend = working_backend or _resolve_registry_working_backend()
    if backend is None:
        return None
    try:
        key = f"{_KEY_PREFIX}:{tier}"
        record = await backend.get(tier, key)
        if record is None:
            return None
        if isinstance(record, dict):
            return record.get("value")
        return str(record)
    except Exception as exc:
        log.debug("read_last_write: tier=%s read failed: %s", tier, exc)
        return None
