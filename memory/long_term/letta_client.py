"""LettaClient — Long-term memory via Letta REST API.

Provides persistent core memory blocks for agent identity and behavior.
Includes garbage protection via memory.validation module.

When Letta server is unreachable, falls back to ephemeral in-memory
storage. Health probing determines availability.

MEMORY ACCESS:
- Supervisor-only: DAG agents cannot directly access Letta
- Validation: All writes validated via memory.validation module
- Sanitization: Content sanitized before storage

LETTA API ENDPOINTS (v0.5+):
- GET /v1/agents/{agent_id}: Get agent info including memory
- PUT /v1/agents/{agent_id}/core-memory: Update agent core memory
- GET /v1/agents/{agent_id}/messages: Get agent message history
- POST /v1/agents/{agent_id}/messages: Send message to agent

REGISTRY INJECTION (PHASE 4):
=============================
LettaConfig can be passed via constructor for dependency injection.
Helper functions accept optional LettaConfig parameter.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import httpx

from memory.long_term.letta_fallback import _InContextFallback
from memory.long_term.letta_health import LettaHealthProbe
from memory.long_term.letta_ops_store import do_delete, do_store
from memory.long_term.letta_registry import LettaAgentRegistry
from memory.shared.types import MemoryEntry, MemoryEntryMetadata, MemoryLayer
from memory.shared.validation import sanitize_content, validate_memory_write

if TYPE_CHECKING:
    from config.settings import LettaConfig
    from memory.shared.types import AgentRole, MemoryKey

log = logging.getLogger("goat2.memory.letta")

__all__ = ["LettaClient"]


def _get_letta_base_url(letta_config: "LettaConfig | None" = None) -> str:
    """
    Get Letta base URL from settings or provided config.

    Args:
        letta_config: Optional LettaConfig. If None, imports from settings.

    Returns:
        Letta base URL string.
    """
    if letta_config is None:
        from config.settings import Settings
        return Settings().letta.base_url
    return letta_config.base_url


def _get_letta_headers(letta_config: "LettaConfig | None" = None) -> dict[str, str]:
    """
    Get Letta API headers from settings or provided config.

    Args:
        letta_config: Optional LettaConfig. If None, imports from settings.

    Returns:
        Dictionary of HTTP headers for Letta API requests.
    """
    if letta_config is None:
        from config.settings import Settings
        return Settings().letta.headers
    return letta_config.headers


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
    - Agent memory accessed via /v1/agents/{agent_id}/core-memory
    - Message history via /v1/agents/{agent_id}/messages
    - Graceful fallback on 404/endpoint errors
    """

    def __init__(
        self,
        probe: LettaHealthProbe | None = None,
        registry: LettaAgentRegistry | None = None,
        fallback: _InContextFallback | None = None,
        letta_config: "LettaConfig | None" = None,
    ) -> None:
        """
        Initialize LettaClient with optional dependencies.

        Args:
            probe: Optional LettaHealthProbe instance.
            registry: Optional LettaAgentRegistry instance.
            fallback: Optional _InContextFallback instance.
            letta_config: Optional LettaConfig for dependency injection.
                         If None, uses LettaHealthProbe's config or imports from settings.
        """
        self._probe = probe or LettaHealthProbe(letta_config)
        self._registry = registry or LettaAgentRegistry(self._probe)
        self._fallback = fallback or _InContextFallback()
        self._http_client: httpx.AsyncClient | None = None
        self._api_version: str | None = None
        self._letta_config: "LettaConfig | None" = letta_config

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client for Letta API calls."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=_get_letta_base_url(self._letta_config),
                headers=_get_letta_headers(self._letta_config),
                timeout=30.0,
            )
        return self._http_client

    async def _detect_api_version(self) -> str:
        """Detect Letta API version by checking available endpoints."""
        if self._api_version:
            return self._api_version

        client = await self._get_http_client()
        try:
            # Try to get server info
            response = await client.get("/api/status")
            if response.status_code == 200:
                data = response.json()
                self._api_version = data.get("version", "unknown")
                log.info("Letta API version: %s", self._api_version)
                return self._api_version
        except Exception:
            pass

        # Default to v0.5+ API structure
        self._api_version = "0.5+"
        return self._api_version

    async def search(
        self,
        agent_role: str,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Search Letta agent messages by semantic query.

        Uses Letta's message history API. Falls back to empty list
        if Letta is unavailable or endpoint doesn't exist.

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

            # Get message history (Letta v0.5+ API)
            response = await client.get(
                f"/v1/agents/{agent_id}/messages",
                params={"limit": limit},
            )

            if response.status_code == 404:
                log.debug("Letta messages endpoint not available; using fallback")
                return self._fallback.search(agent_role, query, limit, tags)

            response.raise_for_status()
            data = response.json()

            # Convert Letta messages to MemoryEntry format
            entries = []
            # Letta 0.16.8 returns a list directly, not a dict
            if isinstance(data, list):
                messages = data
            else:
                messages = data.get("messages", [])
            for msg in messages[:limit]:
                content = msg.get("content", "")
                if isinstance(content, dict):
                    content = str(content.get("text", content))
                entries.append(MemoryEntry(
                    id=msg.get("id", ""),
                    agent_role=agent_role,
                    key=msg.get("id", ""),
                    content=content,
                    source="letta",
                    created_at=msg.get("created_at", ""),
                    metadata={"tags": msg.get("tags", [])},
                ))
            return entries

        except httpx.HTTPError as e:
            log.warning("Letta search HTTP error: %s", e)
            return self._fallback.search(agent_role, query, limit, tags)
        except Exception as e:
            log.warning("Letta search failed: %s", e)
            return self._fallback.search(agent_role, query, limit, tags)

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
            return self._fallback.store(agent_role, key, value, metadata)

        if not await self._probe.is_available():
            log.debug("Letta unavailable; using fallback for %s", key)
            return self._fallback.store(agent_role, key, value, metadata)

        agent_id = await self._registry.get_agent_id(agent_role)

        # Build metadata and user_tags from the provided metadata dict
        meta_dict = dict(metadata) if metadata else {}
        meta_dict.pop("tags", None)  # remove to avoid duplicate
        meta = MemoryEntryMetadata(
            tags=metadata.get("tags", []) if metadata else [],
            **meta_dict,
        )
        user_tags: list[str] = metadata.get("user_tags", []) if metadata else []

        return await do_store(
            self._probe,
            self._registry,
            self._fallback,
            agent_role,
            key,
            value,
            meta,
            user_tags,
        )

    async def retrieve(
        self,
        agent_role: str,
        key: str,
    ) -> MemoryEntry | None:
        """Retrieve a value from Letta memory by key.

        Searches both core memory (blocks) and archival memory (passages)
        to find entries stored by promote().
        """
        if not await self._probe.is_available():
            return self._fallback.retrieve(agent_role, key)

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Get agent core memory (Letta v0.5+ API)
            response = await client.get(f"/v1/agents/{agent_id}/core-memory")

            if response.status_code == 404:
                log.debug("Letta core memory endpoint not available; searching archival")
                # Fall through to archival search
            elif response.raise_for_status():
                data = response.json()

                # Find the block by key/label in core memory
                core_memory = data.get("core_memory", [])
                if isinstance(core_memory, dict):
                    # Some API versions return dict instead of list
                    core_memory = [
                        {"label": k, "value": v} for k, v in core_memory.items()
                    ]

                for block in core_memory:
                    label = block.get("label") or block.get("name", "")
                    if label == key:
                        return MemoryEntry(
                            id=f"letta_core_{agent_role}_{key}",
                            agent_role=agent_role,
                            key=key,
                            content=block.get("value", ""),
                            source="letta",
                            created_at="",
                            metadata={"tags": []},
                        )

            # Key not in core memory — search archival memory
            # Look for passage with [KEY:key] tag
            try:
                search_response = await client.get(
                    f"/v1/agents/{agent_id}/archival-memory",
                    params={"search": f"[KEY:{key}]", "limit": 5},
                )
                if search_response.status_code == 200:
                    raw = search_response.json()
                    passages = raw if isinstance(raw, list) else raw.get("results", raw.get("passages", []))
                    for passage in passages:
                        passage_text = passage.get("text", "")
                        # Check if this passage contains [KEY:key]
                        if f"[KEY:{key}]" in passage_text:
                            return MemoryEntry(
                                id=f"letta_archival_{passage.get('id', key)}",
                                agent_role=agent_role,
                                key=key,
                                content=passage_text,
                                source="letta",
                                created_at=passage.get("created_at", ""),
                                metadata={"tags": passage.get("tags", [])},
                            )
            except Exception as archival_err:
                log.debug("Letta archival search failed: %s", archival_err)

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
        """List entries in Letta memory.

        Returns both core memory blocks and archival passages,
        merged into a single list with deduplication by key.
        """
        if not await self._probe.is_available():
            return self._fallback.list(agent_role, limit)

        entries: list[MemoryEntry] = []
        seen_keys: set[str] = set()

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Get core memory blocks (Letta v0.5+ API)
            try:
                response = await client.get(f"/v1/agents/{agent_id}/core-memory")
                if response.status_code == 200:
                    data = response.json()
                    core_memory = data.get("core_memory", [])
                    if isinstance(core_memory, dict):
                        core_memory = [
                            {"label": k, "value": v} for k, v in core_memory.items()
                        ]

                    for block in core_memory:
                        label = block.get("label") or block.get("name", "")
                        if label and label not in seen_keys:
                            seen_keys.add(label)
                            entries.append(MemoryEntry(
                                id=block.get("label", str(uuid.uuid4())),
                                agent_role=agent_role,
                                key=label,
                                content=block.get("value", ""),
                                source="letta",
                                created_at="",
                                metadata={"tags": []},
                            ))
            except Exception as core_err:
                log.debug("Letta core memory list failed: %s", core_err)

            # Get archival memory passages
            try:
                arch_response = await client.get(
                    f"/v1/agents/{agent_id}/archival-memory",
                    params={"limit": limit},
                )
                if arch_response.status_code == 200:
                    raw = arch_response.json()
                    passages = raw if isinstance(raw, list) else raw.get("results", raw.get("passages", []))
                    for passage in passages:
                        # Extract key from passage text [KEY:xxx]
                        text = passage.get("text", "")
                        key_prefix = "[KEY:"
                        if key_prefix in text:
                            key_start = text.find(key_prefix) + len(key_prefix)
                            key_end = text.find("]", key_start)
                            if key_end > key_start:
                                key = text[key_start:key_end]
                            else:
                                key = passage.get("id", str(uuid.uuid4()))
                        else:
                            key = passage.get("id", str(uuid.uuid4()))

                        if key and key not in seen_keys:
                            seen_keys.add(key)
                            entries.append(MemoryEntry(
                                id=f"letta_archival_{passage.get('id', key)}",
                                agent_role=agent_role,
                                key=key,
                                content=text,
                                source="letta",
                                created_at=passage.get("created_at", ""),
                                metadata={"tags": passage.get("tags", [])},
                            ))
            except Exception as arch_err:
                log.debug("Letta archival memory list failed: %s", arch_err)

            return entries[:limit]

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

            # Get agent memory (Letta v0.5+ API)
            response = await client.get(f"/v1/agents/{agent_id}/core-memory")

            if response.status_code == 404:
                log.debug("Letta memory endpoint not available; using fallback")
                # Check fallback store - use retrieve() not get()
                fallback_entry = self._fallback.retrieve(agent_role, label)
                return fallback_entry.content if fallback_entry else None

            response.raise_for_status()
            data = response.json()

            # Find the block by label in core memory
            core_memory = data.get("core_memory", [])
            if isinstance(core_memory, dict):
                # Some API versions return dict instead of list
                return core_memory.get(label)

            for block in core_memory:
                block_label = block.get("label") or block.get("name", "")
                if block_label == label:
                    return block.get("value")

            log.debug("Core memory block '%s' not found for agent %s", label, agent_id)
            return None

        except httpx.HTTPError as e:
            log.warning("Letta get_block HTTP error: %s", e)
            # Check fallback store on error - use retrieve() not get()
            fallback_entry = self._fallback.retrieve(agent_role, label)
            return fallback_entry.content if fallback_entry else None
        except Exception as e:
            log.warning("Letta get_block failed: %s", e)
            fallback_entry = self._fallback.retrieve(agent_role, label)
            return fallback_entry.content if fallback_entry else None

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
            self._fallback.store(agent_role, label, value)
            return True  # Fallback succeeded

        if not await self._probe.is_available():
            log.debug("Letta unavailable; using fallback for block %s", label)
            self._fallback.store(agent_role, label, value)
            return True

        try:
            agent_id = await self._registry.get_agent_id(agent_role)
            client = await self._get_http_client()

            # Letta 0.16.8: PATCH /v1/agents/{id}/core-memory/blocks/{label}
            response = await client.patch(
                f"/v1/agents/{agent_id}/core-memory/blocks/{label}",
                json={"value": value},
                timeout=10.0,
            )

            if response.status_code == 404:
                log.debug("Letta block '%s' not found; using fallback", label)
                self._fallback.store(agent_role, label, value)
                return True

            response.raise_for_status()
            log.debug("Letta set_block success: %s", label)
            return True

        except httpx.TimeoutException as e:
            log.warning("Letta set_block timed out for block '%s'; using fallback", label)
            self._fallback.store(agent_role, label, value)
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