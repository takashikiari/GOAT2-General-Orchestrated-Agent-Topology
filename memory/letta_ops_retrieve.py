from __future__ import annotations

import logging

from memory.letta_fallback import _InContextFallback
from memory.letta_health import LettaHealthProbe
from memory.letta_helpers import _extract_passages, _parse_passage_text, _passage_to_entry
from memory.letta_registry import LettaAgentRegistry
from memory.types import AgentRole, MemoryEntry, MemoryKey

log = logging.getLogger("goat2.memory.letta")


async def do_retrieve(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    fallback: _InContextFallback, agent_role: AgentRole, key: MemoryKey,
) -> MemoryEntry | None:
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        resp     = await http.get(
            f"/v1/agents/{agent_id}/archival-memory",
            params={"search": f"[KEY:{key}]", "limit": 5},
        )
        resp.raise_for_status()
        for p in _extract_passages(resp.json()):
            if _parse_passage_text(p.get("text") or "")[0] == key:
                return _passage_to_entry(p, agent_role, str(key))
        return None
    except Exception as exc:
        log.warning("retrieve(%s, %s) Letta error: %s", agent_role, key, exc)
        probe.mark_unavailable()
        return fallback.retrieve(agent_role, key)


async def do_search(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    fallback: _InContextFallback, agent_role: AgentRole,
    query: str, limit: int, tags: list[str] | None,
) -> list[MemoryEntry]:
    """Keyword search via GET /archival-memory?search={word}. Semantic search is not used
    because Letta agents have no embedding configured — adding one breaks archival-memory
    POST with a 500 when the Letta server cannot reach the embedding API."""
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        kw = max(
            (w.strip("?.,!;:'\"") for w in query.split() if len(w) > 3),
            key=len, default=query[:30],
        )
        resp = await http.get(
            f"/v1/agents/{agent_id}/archival-memory",
            params={"search": kw, "limit": limit},
        )
        resp.raise_for_status()
        return [_passage_to_entry(p, agent_role, query) for p in _extract_passages(resp.json())]
    except Exception as exc:
        log.warning("search(%s, %r) Letta error: %s", agent_role, query[:60], exc)
        probe.mark_unavailable()
        return fallback.search(agent_role, query, limit, tags)
