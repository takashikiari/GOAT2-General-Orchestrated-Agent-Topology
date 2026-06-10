"""Letta archival-memory retrieve and search operations.

Both functions use the agent-scoped path /v1/agents/{agent_id}/archival-memory.
Global archival-memory endpoints are never used — they 404 without an agent ID.
"""
from __future__ import annotations

import logging

from config.limits import MAX_RECALL_LIMIT
from config.timeouts import LETTA_TIMEOUT
from memory.long_term.letta_fallback import _InContextFallback
from memory.long_term.letta_health import LettaHealthProbe
from memory.long_term.letta_helpers import _extract_passages, _parse_passage_text, _passage_to_entry
from memory.long_term.letta_registry import LettaAgentRegistry
from memory.shared.types import AgentRole, MemoryEntry, MemoryKey

log = logging.getLogger("goat2.memory.letta")


async def do_retrieve(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    fallback: _InContextFallback, agent_role: AgentRole, key: MemoryKey,
) -> MemoryEntry | None:
    """Retrieve a single passage by exact key from archival-memory.

    Searches GET /v1/agents/{agent_id}/archival-memory?search=[KEY:{key}]
    and returns the first passage whose parsed key matches exactly.
    Falls back to _InContextFallback on any error.
    """
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        resp     = await http.get(
            f"/v1/agents/{agent_id}/archival-memory",
            params={"search": f"[KEY:{key}]", "limit": MAX_RECALL_LIMIT},
            timeout=LETTA_TIMEOUT,
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
    """Keyword search via GET /v1/agents/{agent_id}/archival-memory?search={kw}.

    Semantic search is not used because Letta agents have no embedding configured —
    adding one breaks archival-memory POST with 500 when the embedding API is unreachable.
    Debug logging at DEBUG level shows agent_id, keyword, HTTP status, and raw body.
    """
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        kw = max(
            (w.strip("?.,!;:'\"") for w in query.split() if len(w) > 3),
            key=len, default=query[:30],
        )
        log.debug("do_search: agent=%s kw=%r limit=%d", agent_id, kw, limit)
        resp = await http.get(
            f"/v1/agents/{agent_id}/archival-memory",
            params={"search": kw, "limit": limit},
            timeout=LETTA_TIMEOUT,
        )
        log.debug("do_search: status=%d body=%.300s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        return [_passage_to_entry(p, agent_role, query) for p in _extract_passages(resp.json())]
    except Exception as exc:
        log.warning("search(%s, %r) Letta error: %s", agent_role, query[:60], exc)
        probe.mark_unavailable()
        return fallback.search(agent_role, query, limit, tags)
