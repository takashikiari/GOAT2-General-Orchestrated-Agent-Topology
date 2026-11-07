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


def _with_seed_neighbours(merged: list[dict], limit: int) -> list[dict]:
    """The top ``limit`` original results (unchanged) plus the wave-2 neighbours of those results.

    retrieve() puts neighbours after all originals, so a plain ``merged[:limit]`` dropped them: in the
    2026-11-07 memory test 45 of 47 neighbours never reached the model. The originals shown are exactly
    the ones shown before (same rule as fit_originals_first: wave 2 never displaces an original); each
    neighbour is placed right after its seed, and an entry whose parent message is already shown is skipped.
    """
    def is_w2(r):
        return "wave2" in (r.get("mechanisms") or [])

    def mid(r):
        return (r.get("metadata") or {}).get("message_id")

    originals = [r for r in merged if not is_w2(r)][:limit]
    shown_ids = {mid(r) for r in originals} - {None}
    by_seed: dict = {}
    for n in merged:
        if is_w2(n) and n.get("wave2_of") in shown_ids and mid(n) not in shown_ids:
            by_seed.setdefault(n.get("wave2_of"), []).append(n)
    out, seen = [], set()
    for r in originals:
        out.append(r)
        for n in by_seed.get(mid(r), []):
            if mid(n) not in seen:
                seen.add(mid(n))
                out.append(n)
    return out


def _log_wave2(chat_id: str, merged: list[dict], shown: list[dict]) -> None:
    """Measurement only: wave-2 neighbours found by this tool call vs actually shown to the model.

    Separate from l3_usage (which covers only the context injected at turn start). Never raises.
    """
    try:
        def w2(rs):
            return [r for r in rs if "wave2" in (r.get("mechanisms") or [])]
        found, sent = w2(merged), w2(shown)
        log.info("search_memory_wave2 chat=%s shown=%d wave2_found=%d wave2_shown=%d wave2_cut_by_limit=%d "
                 "wave2_shown_ranks=%s", chat_id, len(shown), len(found), len(sent), len(found) - len(sent),
                 [i + 1 for i, r in enumerate(shown) if r in sent])
    except Exception:  # noqa: BLE001
        log.debug("search_memory_wave2 measurement failed", exc_info=True)


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
        results = _with_seed_neighbours(merged, _LIMIT)
        log.debug("search_memory chat=%s query=%r hits=%d", chat_id, query, len(results))
        _log_wave2(chat_id, merged, results)
        if not results:
            return "No relevant memories found."
        return _fmt_results(results)

    return ToolDefinition(
        name="search_memory",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        handler=handler,
    )
