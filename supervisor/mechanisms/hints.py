"""Hints — short text snippets the LLM can use as soft guidance.

Pure Python, no LLM, no regex. Two sources of hints:

  1. Behavioral corrections (``corrections.recall_corrections``).
  2. Static operational hints the codebase owner wants always
     present (loaded from ``config/goat.toml [hints]``).

USAGE:
    from supervisor.mechanisms.hints import build_hints

    hints: list[str] = await build_hints(mm, intent, registry)
    # → [corrections...] + [static_hints...]
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from supervisor.mechanisms.corrections import recall_corrections

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.mechanisms.hints")

__all__ = ["build_hints", "load_static_hints"]

# Section name in config/goat.toml. Each entry is one hint line.
_HINTS_SECTION: str = "hints"


def load_static_hints(registry: "ServiceRegistry | None") -> list[str]:
    """Read ``[hints]`` lines from config/goat.toml.

    Returns a list of trimmed, non-empty strings. Missing file
    or section → empty list. The list preserves toml order.

    Args:
        registry: ServiceRegistry (for config access); ignored
            if absent — the function falls back to ``[]``.
    """
    if registry is None:
        return []
    try:
        from config.modular_loader import load_goat_config
        data = load_goat_config() or {}
        section = data.get(_HINTS_SECTION, {})
        if not isinstance(section, dict):
            return []
        return [str(v).strip() for v in section.values() if str(v).strip()]
    except Exception as exc:  # noqa: BLE001
        log.debug("load_static_hints: toml load failed: %s", exc)
        return []


async def build_hints(
    mm: "MemoryManager | None",
    intent: str,
    registry: "ServiceRegistry | None" = None,
    limit: int = 3,
) -> list[str]:
    """Assemble the full hint list for this turn.

    Order: behavioral corrections first (highest signal), then
    static operational hints. ``intent`` is accepted for
    future intent-aware hint selection; currently unused.

    Args:
        mm: Registry's MemoryManager (or None).
        intent: Raw user intent for this turn (reserved).
        registry: ServiceRegistry for static-hint lookup.
        limit: Max number of correction hints.

    Returns:
        List of hint strings. Always a list; never raises.
    """
    _ = intent  # reserved for future intent-aware hints
    out: list[str] = []
    try:
        out.extend(await recall_corrections(mm, limit=limit))
    except Exception as exc:  # noqa: BLE001
        log.debug("build_hints: corrections failed: %s", exc)
    out.extend(load_static_hints(registry))
    return out