"""
tools.memory_tools — the search_memory tool: GOAT's organic, on-demand path
to L3 (episodic memory).

GOAT calls this within its single LLM turn when it judges that the visible
context (L0+L1+L2) does not contain what it needs — no separate classification
step, no forced call. The model decides in its normal reasoning, exactly like
any other tool (e.g. add_numbers). This is Step 4 of the layered-memory build.

The handler uses the UNCACHED ``search_episodic`` so explicit, user-driven
searches always return fresh results. The L2.5 session cache serves the
future automatic/prefetch retrieval path, not this tool.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)

__all__ = ["build_search_memory_tool"]


def build_search_memory_tool(memory_layers: "MemoryLayers") -> ToolDefinition:
    """Build the search_memory tool, bound to a ``MemoryLayers`` instance.

    This is GOAT's only path to L3 (deep episodic search). GOAT calls it
    organically when it judges that the visible context (L0+L1+L2) doesn't
    contain what it needs — no separate classification step, no forced call.
    The model decides within its normal reasoning, in the same turn as its
    response.

    Args:
        memory_layers: The ``MemoryLayers`` instance the handler searches
            through. Bound at build time; the returned ``ToolDefinition``
            carries the closure.

    Returns:
        A ``ToolDefinition`` named ``search_memory`` whose async handler takes
        a ``query`` string and returns formatted memory hits, or a no-match
        notice.
    """

    async def handler(query: str) -> str:
        """Search episodic memory for ``query``; return formatted hits or a no-match notice.

        Returns one ``- {content}`` line per result (closest first), capped by
        the retrieval budget, or ``"No relevant memories found."`` when empty.
        """
        results = await memory_layers.search_episodic(query)
        log.debug("search_memory query=%r hits=%d", query, len(results))
        if not results:
            return "No relevant memories found."
        return "\n".join(f"- {r['content']}" for r in results)

    return ToolDefinition(
        name="search_memory",
        description=(
            "Search episodic memory (past conversations, history) for "
            "context not visible in the current conversation. Use this "
            "when the user references something that might have been "
            "discussed before but you don't see it above — e.g. a name, "
            "a decision, a fact established earlier. Don't use this for "
            "general knowledge questions — only for things specific to "
            "this user's history with you."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
            },
            "required": ["query"],
        },
        handler=handler,
    )