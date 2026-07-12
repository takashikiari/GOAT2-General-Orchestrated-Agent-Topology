"""tools.memory_tools — search_memory: GOAT's on-demand path to L3 (episodic memory)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from memory.retrieval import retrieve
from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)
__all__ = ["build_search_memory_tool"]

# On-demand result cap shown to the LLM. retrieve()'s cold path already
# merges/boosts/reranks a wider candidate pool internally (its own hardcoded
# limit, memory.config.PREFETCH_MAX_RESULTS); this just trims the final
# answer for one explicit tool call, matching this tool's previous default
# (search_episodic's limit=5). Deliberately NOT PREFETCH_MAX_RESULTS: prefetch
# fills a background context budget, this answers one targeted question.
_LIMIT = 5

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
    """Parse an ISO 8601 string to an epoch second, pining naive input to UTC.

    L3 timestamps are stored as ``time.time()`` (UTC epoch). A naive ISO string
    (no offset) must be read as UTC, not local time, or the filter window shifts
    by the host's UTC offset on a non-UTC server. Aware strings are honored as-is.
    """
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


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
        # Same pipeline the prefetch daemon uses: merge (semantic + BM25 +
        # temporal) -> entity-overlap boost -> cross-encoder rerank. Replaces
        # the old bare search_episodic() call, which had none of that.
        merged, _cache_hit, _cache_key, _meta = await retrieve(
            memory_layers, chat_id, query, state="cold", activation=None,
        )
        # retrieve() has no after/before knobs of its own (its temporal
        # routing only auto-detects date expressions inside the query text,
        # see memory.temporal_route.parse_interval) — an explicit range from
        # the caller is applied as a post-filter over the already
        # merged/boosted/reranked pool.
        if after_ts is not None or before_ts is not None:
            merged = [
                r for r in merged
                if (after_ts is None or r["metadata"].get("timestamp", 0) >= after_ts)
                and (before_ts is None or r["metadata"].get("timestamp", 0) <= before_ts)
            ]
        results = merged[:_LIMIT]
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
