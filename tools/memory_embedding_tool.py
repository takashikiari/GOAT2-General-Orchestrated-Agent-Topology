"""Memory embedding tool — retrieve embedding vectors from storage backends.

Provides MEMORY_EMBEDDING ToolDefinition to get embedding vectors
for memory entries from episodic (ChromaDB) or long-term (Letta) tiers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents.base_agent import ToolDefinition
from config.roles import GOAT_ROLE
from tools.memory_helpers import format_memory_error, validate_tier, ALL_TIERS
from tools.registry_accessor import get_registry

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["MEMORY_EMBEDDING"]


async def _embedding_handler(
    key: str,
    tier: Literal["episodic", "long_term"] = "episodic",
    role: str = GOAT_ROLE,
    memory_manager: "MemoryManager | None" = None,
) -> dict:
    """Get embedding vector for a memory entry.

    Args:
        key: Exact memory key to get embedding for.
        tier: Source tier - 'episodic' (ChromaDB) or 'long_term' (Letta).
        role: Caller role (for access control).
        memory_manager: Optional injected MemoryManager.

    Returns:
        JSON object with key, tier, dimensions, and vector preview.
    """
    error = validate_tier(tier, ["episodic", "long_term"])
    if error:
        return {"error": error}

    if memory_manager is None:
        registry = get_registry()
        memory_manager = registry.memory_manager

    try:
        # Get the entry first to verify it exists
        entry = await memory_manager.locate(GOAT_ROLE, key, memory_type=tier)
        if not entry:
            return {"error": f"Key not found: {key!r}"}

        # Get embedding based on tier
        if tier == "episodic":
            # Get embedding from ChromaDB document
            embedding = await memory_manager._get_episodic_embedding(GOAT_ROLE, key)
            source = "chromadb"
        elif tier == "long_term":
            # Get embedding from Letta
            embedding = await memory_manager._get_long_term_embedding(GOAT_ROLE, key)
            source = "letta"
        else:
            return {"error": f"Unsupported tier: {tier}"}

        if embedding is None:
            return {"error": f"No embedding found for key: {key!r}"}

        # Return with preview (first 10 dimensions)
        vector_preview = embedding[:10].tolist() if hasattr(embedding, 'tolist') else list(embedding[:10])
        dimensions = len(embedding)

        return {
            "key": key,
            "tier": tier,
            "dimensions": dimensions,
            "source": source,
            "vector_preview": vector_preview,
        }
    except Exception as exc:
        return {"error": format_memory_error("memory_embedding", exc)}


MEMORY_EMBEDDING = ToolDefinition(
    name="memory_embedding",
    description="Get embedding vector for a memory entry from episodic (ChromaDB) or long-term (Letta) tier.",
    parameters={
        "type": "object",
        "required": ["key"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Exact memory key to get embedding for.",
            },
            "tier": {
                "type": "string",
                "enum": ["episodic", "long_term"],
                "description": "Source tier - 'episodic' (ChromaDB) or 'long_term' (Letta).",
                "default": "episodic",
            },
        },
    },
    handler=_embedding_handler,
)