from __future__ import annotations

import logging

from memory.long_term.letta_fallback import _InContextFallback
from memory.long_term.letta_health import LettaHealthProbe
from memory.long_term.letta_helpers import _extract_passages, _now_iso, _parse_passage_text
from memory.long_term.letta_registry import LettaAgentRegistry
from memory.shared.types import AgentRole, EntryId, IsoTimestamp, MemoryEntry, MemoryEntryMetadata

log = logging.getLogger("goat2.memory.letta")


async def do_list(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    fallback: _InContextFallback, agent_role: AgentRole, limit: int,
) -> list[MemoryEntry]:
    """Fetch the most recent `limit` archival-memory passages for agent_role from Letta."""
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        resp     = await http.get(
            f"/v1/agents/{agent_id}/archival-memory",
            params={"limit": limit, "ascending": False},
        )
        resp.raise_for_status()
        entries: list[MemoryEntry] = []
        for p in _extract_passages(resp.json()):
            found_key, content = _parse_passage_text(p.get("text") or "")
            entries.append(MemoryEntry(
                id=EntryId(p.get("id") or ""), agent_role=agent_role,
                key=found_key, content=content,
                metadata=MemoryEntryMetadata(tags=p.get("tags") or []),
                created_at=IsoTimestamp(p.get("created_at") or _now_iso()),
                source="letta",
            ))
        return entries
    except Exception as exc:
        log.warning("list(%s) Letta error: %s", agent_role, exc)
        probe.mark_unavailable()
        return fallback.list(agent_role, limit)


async def do_clear(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    fallback: _InContextFallback, agent_role: AgentRole,
) -> int:
    """Delete all archival-memory passages for agent_role. Fetches IDs first, then deletes individually."""
    try:
        entries  = await do_list(probe, registry, fallback, agent_role, limit=500)
        if not entries:
            return 0
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        count    = 0
        for entry in entries:
            if not entry.id:
                continue
            resp = await http.delete(
                f"/v1/agents/{agent_id}/archival-memory/{entry.id}"
            )
            if resp.is_success:
                count += 1
        log.info("clear(%s): deleted %d passages", agent_role, count)
        return count
    except Exception as exc:
        log.warning("clear(%s) Letta error: %s", agent_role, exc)
        probe.mark_unavailable()
        return fallback.clear(agent_role)
