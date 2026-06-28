"""tools.memory_tools — search_memory: GOAT's on-demand path to L3 (episodic memory)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)
__all__ = ["build_search_memory_tool"]

_DESCRIPTION = (
    "Search episodic memory (past conversations, history) for context not visible "
    "in the current conversation. Use this when the user references something that "
    "might have been discussed before — e.g. a name, a decision, a fact established "
    "earlier. Supports optional time range filtering via `after`/`before` (ISO 8601, "
    "e.g. '2026-06-27T16:00:00'). Don't use for general knowledge — only for this "
    "user's history with you."
)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "query":  {"type": "string", "description": "What to search for"},
        "after":  {"type": "string", "description": "ISO 8601 start (inclusive), e.g. 2026-06-27T16:00:00"},
        "before": {"type": "string", "description": "ISO 8601 end (inclusive), e.g. 2026-06-27T18:00:00"},
    },
    "required": ["query"],
}


def _iso_to_ts(s: str) -> float | None:
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _fmt_results(results: list[dict]) -> str:
    lines = []
    for r in results:
        ts = r["metadata"].get("timestamp", 0)
        seq = r["metadata"].get("sequence_number", "")
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
        seq_tag = f" #{seq}" if seq else ""
        lines.append(f"[{dt}{seq_tag}] {r['content']}")
    return "\n".join(lines)


def build_search_memory_tool(memory_layers: "MemoryLayers") -> ToolDefinition:
    """Build the search_memory tool bound to a MemoryLayers instance."""

    async def handler(query: str, after: str = "", before: str = "", chat_id: str = "") -> str:
        after_ts = _iso_to_ts(after) if after else None
        before_ts = _iso_to_ts(before) if before else None
        results = await memory_layers.search_episodic(query, after=after_ts, before=before_ts)
        log.debug("search_memory chat=%s query=%r hits=%d", chat_id, query, len(results))
        if not results:
            return "No relevant memories found."
        return _fmt_results(results)

    return ToolDefinition(
        name="search_memory",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        handler=handler,
    )
