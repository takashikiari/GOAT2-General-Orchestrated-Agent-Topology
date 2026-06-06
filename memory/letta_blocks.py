from __future__ import annotations

import logging

from memory.letta_health import LettaHealthProbe
from memory.letta_helpers import _LettaBlockValue
from memory.letta_registry import LettaAgentRegistry
from memory.types import AgentRole

log = logging.getLogger("goat2.memory.letta")


async def do_get_block(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    agent_role: AgentRole, label: str,
) -> str | None:
    """
    Read a named core-memory block from the Letta agent for agent_role.
    Core blocks are always in the Letta agent's context window — use for compact structured facts.
    """
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        resp     = await http.get(
            f"/v1/agents/{agent_id}/core-memory/blocks/{label}"
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        block: _LettaBlockValue = resp.json()
        return block.get("value")
    except Exception as exc:
        log.warning("get_block(%s, %s) Letta error: %s", agent_role, label, exc)
        probe.mark_unavailable()
        return None


async def do_set_block(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    agent_role: AgentRole, label: str, value: str,
) -> bool:
    """
    Write or update a named core-memory block. Returns True on success.
    Fails silently (returns False) when Letta is unreachable.
    """
    try:
        agent_id  = await registry.get_agent_id(agent_role)
        http      = await probe.get_http()
        token_lim = probe._cfg.memory_token_limit * 4
        resp      = await http.patch(
            f"/v1/agents/{agent_id}/core-memory/blocks/{label}",
            json={"value": value, "limit": token_lim},
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.warning("set_block(%s, %s) Letta error: %s", agent_role, label, exc)
        probe.mark_unavailable()
        return False
