"""Letta agent registry — lazily creates and caches one Letta agent per AgentRole.

AgentRole maps to Letta agent name via prefix: "goat2-{role}".
Agent IDs are resolved dynamically from the Letta API — never hardcoded.
"""
from __future__ import annotations

import asyncio
import logging

from memory.letta_health import LettaHealthProbe
from memory.letta_helpers import _AGENT_NAME_PREFIX, _LettaAgentInfo
from memory.types import AgentRole, LettaAgentId

log = logging.getLogger("goat2.memory.letta")


class LettaAgentRegistry:
    """Lazily creates and caches one Letta agent per AgentRole."""

    __slots__ = ("_probe", "_agent_ids", "_agent_locks")

    def __init__(self, probe: LettaHealthProbe) -> None:
        """Initialise with a health probe; agent ID cache starts empty."""
        self._probe        = probe
        self._agent_ids:   dict[AgentRole, LettaAgentId]  = {}
        self._agent_locks: dict[AgentRole, asyncio.Lock]  = {}

    def _get_lock(self, role: AgentRole) -> asyncio.Lock:
        """Return (creating if needed) a per-role asyncio.Lock for double-check locking."""
        if role not in self._agent_locks:
            self._agent_locks[role] = asyncio.Lock()
        return self._agent_locks[role]

    async def get_agent_id(self, role: AgentRole) -> LettaAgentId:
        """Return the cached agent ID for role, resolving it on first access."""
        if role in self._agent_ids:
            return self._agent_ids[role]
        async with self._get_lock(role):
            if role in self._agent_ids:
                return self._agent_ids[role]
            agent_id              = await self._find_or_create(role)
            self._agent_ids[role] = agent_id
            return agent_id

    async def _find_or_create(self, role: AgentRole) -> LettaAgentId:
        """Find an existing Letta agent by exact name or create a new one.

        Uses limit=5 and name-filters the response — Letta may return all agents
        regardless of the name param, so we must match explicitly to avoid using
        the wrong agent ID (e.g. goat2-user_session instead of goat2-memory).
        """
        http = await self._probe.get_http()
        name = f"{_AGENT_NAME_PREFIX}{role}"
        try:
            resp = await http.get("/v1/agents/", params={"name": name, "limit": 5})
            resp.raise_for_status()
            data   = resp.json()
            agents: list[_LettaAgentInfo] = (
                data if isinstance(data, list) else data.get("agents", [])
            )
            # Match by exact name — never accept agents[0] blindly
            matched = next((a for a in agents if a.get("name") == name), None)
            if matched:
                agent_id = LettaAgentId(matched["id"])
                log.debug("Found existing Letta agent %r → %s", name, agent_id)
                return agent_id
            log.debug("No agent named %r in %d results; creating new", name, len(agents))
        except Exception as exc:
            log.debug("Agent search failed for %r: %s", name, exc)
        return await self._create(role, name)

    async def _create(self, role: AgentRole, name: str) -> LettaAgentId:
        """POST /v1/agents/ — minimal Letta 0.16.8 payload: name + memory_blocks only."""
        http    = await self._probe.get_http()
        cfg     = self._probe._cfg
        # Discover available models from Letta (no hardcoded fallback)
        models_resp = await http.get("/v1/models/")
        models_resp.raise_for_status()
        available_models = models_resp.json()
        if not available_models:
            raise RuntimeError("Letta has no models configured")
        log.info("Using Letta model %s (discovered dynamically)", available_models[0]["handle"])

        payload = {
            "name":  name,
            "model": available_models[0]["handle"],
            "memory_blocks": [
                {
                    "label": "persona",
                    "value": "",  # populated by behavioral style analysis after first session
                    "limit": cfg.memory_token_limit * 4,
                },
                {
                    "label": "human",
                    "value": "",  # populated by info_extract from user messages
                    "limit": cfg.memory_token_limit * 4,
                },
            ],
        }
        try:
            resp = await http.post("/v1/agents/", json=payload)
            resp.raise_for_status()
            agent_id = LettaAgentId(resp.json()["id"])
            log.info("Created Letta agent %r → %s", name, agent_id)
            return agent_id
        except Exception as exc:
            raise RuntimeError(f"Failed to create Letta agent {name!r}: {exc}") from exc
