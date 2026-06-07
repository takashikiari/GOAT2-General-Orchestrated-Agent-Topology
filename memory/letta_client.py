"""LettaClient — Long-term memory via Letta REST API.

Provides persistent core memory blocks for agent identity and behavior.
Includes garbage protection via memory.validation module.

When Letta server is unreachable, falls back to ephemeral in-memory
storage. Health probing determines availability.

MEMORY ACCESS:
- Supervisor-only: DAG agents cannot directly access Letta
- Validation: All writes validated via memory.validation module
- Sanitization: Content sanitized before storage

LETTA API ENDPOINTS:
- GET /api/agents/{agent_id}/core-memory: Retrieve core memory blocks
- PUT /api/agents/{agent_id}/core-memory: Update core memory blocks
- GET /api/agents/{agent_id}/messages: Search agent messages
- GET /api/agents/{agent_id}/memory: Get full memory state
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from memory.letta_fallback import _InContextFallback
from memory.letta_health import LettaHealthProbe
from memory.letta_ops_store import do_delete, do_store
from memory.letta_registry import LettaAgentRegistry
from memory.types import MemoryEntry, MemoryLayer
from memory.validation import sanitize_content, validate_memory_write

if TYPE_CHECKING:
    from memory.types import AgentRole, MemoryKey

log = logging.getLogger("goat2.memory.letta")

__all__ = ["LettaClient", "letta_client"]


class LettaClient(MemoryLayer):
    """
    Persistent memory via the Letta REST API with in-context fallback.

    When Letta server is unreachable, falls back to ephemeral in-memory
    storage (_InContextFallback). Health probing determines availability.

    Implements MemoryLayer Protocol for integration with MemoryManager.

    GARBAGE PROTECTION:
    - All writes validated via validate_memory_write()
    - Content sanitized before storage
    - Size limits enforced (MAX_LETTA_BLOCK_LENGTH)

    LETTA API INTEGRATION:
    - Uses httpx.AsyncClient for HTTP requests
    - Core memory blocks accessed via /api/agents/{agent_id}/core-memory
    - Message search via /api/agents/{agent_id}/messages
    - Full memory state via /api/agents/{agent_id}/memory
    """

    def __init__(
        self,
        probe: LettaHealthProbe | None = None,
        registry: LettaAgentRegistry | None = None,
        fallback: _InContextFallback | None = None,
    ) -> None:
        self._probe = probe or LettaHealthProbe()
        self._registry = registry or LettaAgentRegistry(self._probe)
        self._fallback = fallback or _InContextFallback()
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client for Letta API calls."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=await self._probe.get_base_url(),
                headers=await self._probe.get_headers(),
                timeout=30.0,
            )
        return self._http_client

    async def search(
        self,
        agent_role: str,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Search Letta agent messages by semantic query.

        Uses Letta's message search API to find relevant messages.
        Falls back to empty list if Letta is unavailable.

        Args:
            agent_role: The agent role identifier
            query: Semantic search query
            limit: Maximum results to return
            tags: Optional tag filters (not supported by Letta API)

        Returns:
            List of MemoryEntry objects from search results
        """
        if not await self._probe.is_available():
            log.debug("Letta unavailable for search; returning empty")
            return []

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Search messages via Letta API
            response = await client.get(
                f"/api/agents/{agent_id}/messages",
                params={"query": query, "limit": limit},
            )
            response.raise_for_status()
            data = response.json()

            # Convert Letta messages to MemoryEntry format
            entries = []
            for msg in data.get("messages", [])[:limit]:
                entries.append(MemoryEntry(
                    key=msg.get("id", ""),
                    content=msg.get("content", ""),
                    source="letta",
                    created_at=msg.get("created_at", ""),
                    tags=msg.get("tags", []),
                ))
            return entries

        except httpx.HTTPError as e:
            log.warning("Letta search HTTP error: %s", e)
            return []
        except Exception as e:
            log.warning("Letta search failed: %s", e)
            return []

    async def store(
        self,
        agent_role: str,
        key: str,
        value: str,
        *,
        metadata: dict | None = None,
        ttl: int | None = None,
    ) -> MemoryEntry:
        """Store a value in Letta memory with validation.

        VALIDATION:
        - Key format and length validated
        - Value sanitized and size-checked
        - Rejected if malformed or exceeds limits

        Args:
            agent_role: The agent role identifier.
            key: Memory key (used as block label).
            value: Content to store.
            metadata: Optional metadata (ignored for Letta).
            ttl: Time-to-live (ignored for Letta).

        Returns:
            MemoryEntry on success, fallback entry on failure.
        """
        # Validate before storing
        try:
            validate_memory_write(key, value, tier="long_term", for_letta=True)
            value = sanitize_content(value)
        except ValueError as exc:
            log.warning("Letta store validation failed: %s", exc)
            return self._fallback.store(agent_role, key, value)

        if not await self._probe.is_available():
            log.debug("Letta unavailable; using fallback for %s", key)
            return self._fallback.store(agent_role, key, value)

        agent_id = await self._registry.get_agent_id(agent_role)
        return await do_store(
            self._probe,
            self._registry,
            self._fallback,
            agent_role,
            agent_id,
            key,
            value,
        )

    async def retrieve(
        self,
        agent_role: str,
        key: str,
    ) -> MemoryEntry | None:
        """Retrieve a value from Letta memory by key."""
        if not await self._probe.is_available():
            return self._fallback.retrieve(agent_role, key)

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Get specific memory block
            response = await client.get(
                f"/api/agents/{agent_id}/core-memory",
            )
            response.raise_for_status()
            data = response.json()

            # Find the block by key/label
            blocks = data.get("core_memory", [])
            for block in blocks:
                if block.get("label") == key or block.get("name") == key:
                    return MemoryEntry(
                        key=key,
                        content=block.get("value", ""),
                        source="letta",
                        created_at="",
                        tags=[],
                    )

            return None

        except httpx.HTTPError as e:
            log.warning("Letta retrieve HTTP error: %s", e)
            return self._fallback.retrieve(agent_role, key)
        except Exception as e:
            log.warning("Letta retrieve failed: %s", e)
            return self._fallback.retrieve(agent_role, key)

    async def delete(
        self,
        agent_role: str,
        key: str,
    ) -> bool:
        """Delete a value from Letta memory."""
        if not await self._probe.is_available():
            return self._fallback.delete(agent_role, key)

        agent_id = await self._registry.get_agent_id(agent_role)
        return await do_delete(
            self._probe,
            self._registry,
            self._fallback,
            agent_role,
            key,
        )

    async def list(
        self,
        agent_role: str,
        *,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """List entries in Letta memory."""
        if not await self._probe.is_available():
            return self._fallback.list(agent_role, limit)

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Get full memory state
            response = await client.get(
                f"/api/agents/{agent_id}/memory",
            )
            response.raise_for_status()
            data = response.json()

            # Convert to MemoryEntry list
            entries = []
            core_memory = data.get("core_memory", [])
            for block in core_memory[:limit]:
                entries.append(MemoryEntry(
                    key=block.get("label", block.get("name", "")),
                    content=block.get("value", ""),
                    source="letta",
                    created_at="",
                    tags=[],
                ))
            return entries

        except httpx.HTTPError as e:
            log.warning("Letta list HTTP error: %s", e)
            return self._fallback.list(agent_role, limit)
        except Exception as e:
            log.warning("Letta list failed: %s", e)
            return self._fallback.list(agent_role, limit)

    async def clear(self, agent_role: str) -> int:
        """Clear all entries in Letta memory."""
        if not await self._probe.is_available():
            return self._fallback.clear(agent_role)

        log.warning("Letta clear not fully implemented")
        return 0

    async def health(self) -> bool:
        """Check Letta server health."""
        return await self._probe.is_available()

    async def get_block(
        self,
        agent_role: str,
        label: str,
    ) -> str | None:
        """Read a Letta core-memory block for agent_role.

        Uses the Letta API to retrieve a specific core memory block
        by its label/name. Core memory blocks are always-in-context
        named slots in Letta.

        Args:
            agent_role: The agent role identifier
            label: Block label/name to retrieve

        Returns:
            Block content string, or None if Letta unavailable or block not found
        """
        if not await self._probe.is_available():
            log.debug("Letta unavailable for get_block; returning None")
            return None

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Get core memory blocks
            response = await client.get(
                f"/api/agents/{agent_id}/core-memory",
            )
            response.raise_for_status()
            data = response.json()

            # Find the block by label
            blocks = data.get("core_memory", [])
            for block in blocks:
                if block.get("label") == label or block.get("name") == label:
                    return block.get("value")

            log.debug("Core memory block '%s' not found for agent %s", label, agent_id)
            return None

        except httpx.HTTPError as e:
            log.warning("Letta get_block HTTP error: %s", e)
            return None
        except Exception as e:
            log.warning("Letta get_block failed: %s", e)
            return None

    async def set_block(
        self,
        agent_role: str,
        label: str,
        value: str,
    ) -> bool:
        """Write or update a Letta core-memory block with validation.

        VALIDATION:
        - Label validated as memory key
        - Value sanitized and size-checked
        - Rejected if malformed or exceeds MAX_LETTA_BLOCK_LENGTH

        Args:
            agent_role: The agent role identifier.
            label: Block label/name.
            value: Content to store.

        Returns:
            True on success, False on failure.
        """
        # Validate before storing
        try:
            validate_memory_write(label, value, tier="long_term", for_letta=True)
            value = sanitize_content(value)
        except ValueError as exc:
            log.warning("Letta set_block validation failed: %s", exc)
            return False

        if not await self._probe.is_available():
            log.debug("Letta unavailable; using fallback for block %s", label)
            self._fallback.store(agent_role, label, value)
            return True

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Update core memory block via Letta API
            response = await client.put(
                f"/api/agents/{agent_id}/core-memory",
                json={
                    "label": label,
                    "value": value,
                },
            )
            response.raise_for_status()
            log.debug("Letta set_block success: %s", label)
            return True

        except httpx.HTTPError as e:
            log.warning("Letta set_block HTTP error: %s", e)
            self._fallback.store(agent_role, label, value)
            return True  # Fallback succeeded
        except Exception as e:
            log.warning("Letta set_block failed: %s", e)
            self._fallback.store(agent_role, label, value)
            return True  # Fallback succeeded

    async def close(self) -> None:
        """Close the HTTP client when done."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None


letta_client = LettaClient()
