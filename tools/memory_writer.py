"""
tools.memory_writer — the store_memory tool: GOAT's organic, on-demand path
to WRITE L3 (episodic memory).

GOAT calls this within its single LLM turn when it decides that something
from the conversation is worth preserving for future sessions — no automatic
promotion, no background daemon. GOAT is the sole decision-maker for what
enters episodic memory. This is Step 5 of the layered-memory build.

The handler receives ``chat_id`` injected by the Orchestrator's tool round
(the model never supplies it); the schema exposed to the model lists only
``content`` and ``tags``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)

__all__ = ["build_store_memory_tool"]


def build_store_memory_tool(memory_layers: "MemoryLayers") -> ToolDefinition:
    """Build the store_memory tool, bound to a ``MemoryLayers`` instance.

    GOAT's only path to WRITE L3. GOAT calls it organically when it decides
    something from the conversation should be preserved for future sessions
    — no automatic promotion, no background daemon. The model decides within
    its normal reasoning, in the same turn as its response.

    Args:
        memory_layers: The ``MemoryLayers`` instance the handler writes
            through. Bound at build time; the returned ``ToolDefinition``
            carries the closure.

    Returns:
        A ``ToolDefinition`` named ``store_memory`` whose async handler takes
        ``content`` and optional ``tags`` (plus a ``chat_id`` injected by the
        Orchestrator) and returns a confirmation message.
    """

    async def handler(content: str, tags: str = "", chat_id: str = "") -> str:
        """Store ``content`` in episodic memory with optional tags.

        Args:
            content: The information to store (what GOAT wants to remember).
            tags: Optional comma-separated tags for retrieval.
            chat_id: Origin chat — injected by the Orchestrator, not the model.

        Returns:
            A confirmation message with a summary of the stored content.
        """
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        log.debug("store_memory chat=%s tags=%r content=%r", chat_id, tag_list, content[:80])
        await memory_layers.store_episodic(chat_id, content, tags=tag_list)
        log.info("store_memory chat=%s stored %d chars, tags=%r", chat_id, len(content), tag_list)
        if len(content) > 100:
            return f"✅ Stored in episodic memory: {content[:100]}..."
        return f"✅ Stored: {content}"

    return ToolDefinition(
        name="store_memory",
        description=(
            "Store important information in episodic memory for future "
            "sessions. Use this when the user shares something worth "
            "remembering long-term — preferences, decisions, facts, "
            "project context, important details. Not for temporary notes "
            "or information that's only relevant for this conversation. "
            "GOAT decides what's worth preserving."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to store (what GOAT wants to remember)",
                },
                "tags": {
                    "type": "string",
                    "description": "Optional comma-separated tags for retrieval (e.g. 'project,decision')",
                },
            },
            "required": ["content"],
        },
        handler=handler,
    )