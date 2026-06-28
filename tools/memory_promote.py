"""tools.memory_promote — the promote_memory tool: GOAT's path to WRITE L1.

GOAT calls this when it judges something is a *stable, reusable* fact worth
keeping in permanent core-memory (L1) — as distinct from ``store_memory``, which
writes episodic memory (L3, recency-bounded, grows freely). The promotion is
gated: upsert-by-key + an L1 token cap keep the core-memory block small and
curated, so L1 (always-in-context, off the top of every budget) stays lean.
This is the episodic → Letta permanent promotion step (GOAT-decided, no daemon).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)

__all__ = ["build_promote_memory_tool"]


def build_promote_memory_tool(memory_layers: "MemoryLayers") -> ToolDefinition:
    """Build the promote_memory tool, bound to a ``MemoryLayers`` instance.

    GOAT's only path to WRITE L1 (permanent core-memory). It calls this when it
    decides a fact is stable and reusable across all future sessions — e.g. the
    user's name, role, a stable preference — NOT ephemeral context. The model
    decides within its normal reasoning, in the same turn.

    Args:
        memory_layers: The ``MemoryLayers`` instance the handler promotes
            through. Bound at build time; the returned ``ToolDefinition``
            carries the closure.

    Returns:
        A ``ToolDefinition`` named ``promote_memory`` whose async handler takes
        ``key`` and ``value`` (plus a ``chat_id`` injected by the orchestrator,
        unused — facts are global, not per-chat) and returns a status string.
    """

    async def handler(key: str, value: str, chat_id: str = "") -> str:
        """Promote ``key=value`` into permanent core-memory (L1); return a status string.

        Args:
            key: Fact key (e.g. ``"user_name"``). Re-promoting an existing key
                updates the value (upsert), keeping L1 deduplicated.
            value: Fact value.
            chat_id: Origin chat — injected by the Orchestrator, not the model;
                unused (facts are global across chats).

        Returns:
            A ``✅`` confirmation or a ``❌`` reason (empty key / L1 cap exceeded
            / Letta unavailable). Never raises.
        """
        result = await memory_layers.promote_fact(key, value)
        log.info("promote_memory chat=%s key=%r -> %s", chat_id, key, result[:60])
        return result

    return ToolDefinition(
        name="promote_memory",
        description=(
            "Promote a stable, reusable fact into PERMANENT core-memory (L1) — "
            "kept in context for every future session. Use this only for facts "
            "that are stable and worth always remembering: the user's name, "
            "role, a long-term preference, a permanent project constraint. Do "
            "NOT use this for conversation context or anything that may go "
            "stale — use store_memory (episodic) for those. Re-promoting an "
            "existing key updates it. L1 is small and curated; promotion past "
            "its cap is refused."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Fact key (e.g. 'user_name', 'role', 'preferred_language')",
                },
                "value": {
                    "type": "string",
                    "description": "The stable fact value to remember permanently",
                },
            },
            "required": ["key", "value"],
        },
        handler=handler,
    )