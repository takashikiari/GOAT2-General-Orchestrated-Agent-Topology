"""Memory health tool — quick liveness report for the four backing services.

GOAT can invoke ``memory_health`` conversationally to confirm Redis,
ChromaDB, Letta, and SearXNG are reachable. The handler delegates to
``supervisor.health.health_check`` (parallel probes, 5 s ceiling per
service, never raises) and returns a human-readable summary.

The tool follows the same dependency-injection pattern as the other
memory tools — the ``memory_manager`` parameter is injected by the
tool runtime, and the live ``ServiceRegistry`` is resolved via
``tools.registry_accessor.get_registry()``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.memory_tools.memory_helpers import make_tool

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.tools.memory.health")

__all__ = ["MEMORY_HEALTH"]


def _format_report(report: dict[str, bool]) -> str:
    """Format a health report dict as a multi-line, human-readable string."""
    lines: list[str] = ["Service health report:"]
    for key in ("redis", "chromadb", "letta", "searxng"):
        marker = "OK" if report.get(key) else "DOWN"
        lines.append(f"  {key}: {marker}")
    overall = "OK" if report.get("overall") else "DEGRADED"
    lines.append(f"  overall: {overall}")
    return "\n".join(lines)


async def _handler(memory_manager: "MemoryManager | None" = None) -> str:
    """Probe all four services and return a formatted report.

    Args:
        memory_manager: Optional injected MemoryManager. Currently
            unused — the health probes go through the registry —
            but kept for symmetry with the other memory tools so
            the tool runtime's dependency-injection stays happy.

    Returns:
        Multi-line string with one line per service plus an
        overall summary. Never raises — every error is caught
        inside ``health_check`` itself.
    """
    log.debug("memory_health: probe starting")
    try:
        from supervisor.health import health_check
        from tools.registry_accessor import get_registry
        registry = get_registry()
        report = await health_check(registry)
    except Exception as exc:  # noqa: BLE001 — tool handlers must never raise
        log.exception("memory_health: unexpected error during probe")
        return f"ERROR: health check failed: {exc}"
    return _format_report(report)


MEMORY_HEALTH = make_tool(
    name="memory_health",
    description=(
        "Probe Redis, ChromaDB, Letta, and SearXNG in parallel and "
        "return a per-service liveness report. Each probe has a 5s "
        "ceiling; DOWN means timeout, exception, or HTTP error. "
        "Use this to confirm GOAT's backing services are reachable "
        "before reporting a degradation."
    ),
    parameters={
        "type": "object",
        "required": [],
        "properties": {},
    },
    handler=_handler,
)
