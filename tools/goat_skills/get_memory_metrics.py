"""goat_skills.get_memory_metrics — on-demand live memory-metrics tool.

GOAT calls this when asked about its own memory health (cache hit rate,
prefetch rates, tier hit rates, average latency per stage, average tokens per
tier). Returns the registry-owned ``MemoryAnalytics`` aggregate report as JSON.
On-demand only — no always-on context injection.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

__all__ = ["build"]

_DESCRIPTION = (
    "Live aggregated memory metrics for GOAT itself: cache hit rate, prefetch "
    "attempt/success/timeout rates, tier hit rates, top intents, average "
    "latency per stage (classify/search/assemble/inject), and average tokens "
    "injected per tier. Call this when the user asks how your memory is doing, "
    "about cache hit rate, latency, or memory performance."
)


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Build the get_memory_metrics tool, bound to the registry's analytics."""
    async def handler(chat_id: str = "") -> str:
        """Return the live memory-metrics report as indented JSON."""
        report = registry.memory_analytics.get_report()
        return json.dumps(report, default=str, indent=2)

    return [ToolDefinition(
        name="get_memory_metrics",
        description=_DESCRIPTION,
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )]