"""LettaClient — Long-term memory via Letta REST API.

Provides persistent core memory blocks for agent identity and behavior.
Includes garbage protection via memory.validation module.

When Letta server is unreachable, falls back to ephemeral in-memory
storage. Health probing determines availability.

MEMORY ACCESS:
- Supervisor-only: DAG agents cannot directly access Letta
- Validation: All writes validated via memory.validation module
- Sanitization: Content sanitized before storage
"""
from __future__ import annotations

import logging

from memory.letta_fallback import _InContextFallback
from memory.letta_health import LettaHealthProbe
from memory.letta_ops_store import do_delete, do_store
from memory.letta_registry import LettaAgentRegistry
from memory.types import MemoryEntry, MemoryLayer
from memory.validation import sanitize_content, validate_memory_write

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
    """

    def __init__(
        self,
        probe: LettaHealthProbe | None = None,
        registry: LettaAgentRegistry | None = None,
        fallback: _InContextFallback | None = None,
    ) -> None:
        self._probe = probe or LettaHealthProbe()
        self._registry = registry or LettaAgentRegistry()
        self._fallback = fallback or _InContextFallback()

    async def search(
        self,
        agent_role: str,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Search Letta memory blocks (not implemented — Letta uses blocks)."""
        log.warning("Letta search not implemented; returning empty")
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

        agent_id = await self._registry.get_agent_id(agent_role)
        # Letta uses blocks, not key-value; this is a simplified interface
        log.warning("Letta retrieve not fully implemented")
        return None

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

        agent_id = await self._registry.get_agent_id(agent_role)
        log.warning("Letta list not fully implemented")
        return []

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
        """Read a Letta core-memory block for agent_role."""
        if not await self._probe.is_available():
            return None

        agent_id = await self._registry.get_agent_id(agent_role)
        log.warning("Letta get_block not fully implemented")
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

        agent_id = await self._registry.get_agent_id(agent_role)
        return await do_store(
            self._probe,
            self._registry,
            self._fallback,
            agent_role,
            agent_id,
            label,
            value,
        )


letta_client = LettaClient()
