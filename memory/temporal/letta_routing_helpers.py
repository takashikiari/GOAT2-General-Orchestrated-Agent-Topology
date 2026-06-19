"""Letta routing helpers — safe timeouts and tier-name normalisation.

Extracted from ``memory_helpers.py`` to keep that file under the
260-line ceiling. The helpers here exist because tool handlers
(``memory_search``, ``memory_recent``, ``memory_direct_query``,
``memory_count``) accept tier aliases like ``"letta"`` / ``"all"`` from
the user, but ``MemoryType`` only knows the canonical three names.

USAGE:
    from memory.temporal.letta_routing_helpers import (
        LETA_CALL_TIMEOUT_S, normalize_tier,
        letta_search_safe, letta_list_safe,
    )

    hits = await letta_search_safe(mm, query="...", limit=10)

WHY:
    Without these helpers, a user passing ``tier="letta"`` reached
    ``MemoryType(tier)`` and raised ``ValueError``; without the
    timeout wrappers, a hung Letta call blocked the whole turn. The
    10 s ceiling is the per-call budget surfaced to the LLM tools.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from config.roles import GOAT_ROLE

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.tools.letta_routing")

__all__ = [
    "LETA_CALL_TIMEOUT_S",
    "normalize_tier",
    "letta_search_safe",
    "letta_list_safe",
]


# Per-call ceiling for any Letta operation surfaced through a tool (seconds).
# Read by the LLM-callable tool layer; keeps a hung Letta from blocking
# the turn path. Tunable via config (add to config/memory.toml [letta]
# in a follow-up if needed).
LETA_CALL_TIMEOUT_S: Final[float] = 10.0


def normalize_tier(tier: str) -> str:
    """Translate user-facing tier aliases to internal MemoryType values.

    Mapping:
        "letta" -> "long_term"   (Letta IS the long_term backend)
        "all"   -> "any"         (fan-out trigger in MemoryManager.search)
        anything else (working / episodic / long_term / any) -> unchanged
    """
    if tier == "letta":
        return "long_term"
    if tier == "all":
        return "any"
    return tier


async def letta_search_safe(mm: "MemoryManager", query: str, limit: int) -> list:
    """Search Letta with a hard 10 s ceiling. Never raises.

    Args:
        mm: The registry's MemoryManager (must expose ``.long_term.search``).
        query: Natural-language search query.
        limit: Max results.

    Returns:
        List of Letta hits, or ``[]`` on timeout / failure.
    """
    try:
        return await asyncio.wait_for(
            mm.long_term.search(GOAT_ROLE, query, limit=limit),
            timeout=LETA_CALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("letta_routing: search timed out after %.1fs", LETA_CALL_TIMEOUT_S)
        return []
    except Exception as exc:  # noqa: BLE001 — tool wrapper must never raise
        log.warning("letta_routing: search failed: %s", exc)
        return []


async def letta_list_safe(mm: "MemoryManager", limit: int) -> list:
    """List Letta entries with a hard 10 s ceiling. Never raises.

    Args:
        mm: The registry's MemoryManager.
        limit: Max entries to return.

    Returns:
        List of Letta entries, or ``[]`` on timeout / failure.
    """
    try:
        return await asyncio.wait_for(
            mm.long_term.list(GOAT_ROLE, limit=limit),
            timeout=LETA_CALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("letta_routing: list timed out after %.1fs", LETA_CALL_TIMEOUT_S)
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("letta_routing: list failed: %s", exc)
        return []