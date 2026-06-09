from __future__ import annotations

import logging
import uuid

from memory.letta_fallback import _InContextFallback
from memory.letta_health import LettaHealthProbe
from memory.letta_helpers import (
    _GLOBAL_TAG, _extract_passages, _key_tag,
    _now_iso, _parse_passage_text, _passage_text, _role_tag,
)
from memory.letta_registry import LettaAgentRegistry
from memory.types import (
    AgentRole, EntryId, IsoTimestamp, MemoryEntry,
    MemoryEntryMetadata, MemoryKey,
)

log = logging.getLogger("goat2.memory.letta")


async def do_delete(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    fallback: _InContextFallback, agent_role: AgentRole, key: MemoryKey,
) -> bool:
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http     = await probe.get_http()
        resp     = await http.get(
            f"/v1/agents/{agent_id}/archival-memory",
            params={"search": f"[KEY:{key}]", "limit": 20},
        )
        resp.raise_for_status()
        deleted = 0
        for p in _extract_passages(resp.json()):
            found_key, _ = _parse_passage_text(p.get("text") or "")
            if found_key != key:
                continue
            del_resp = await http.delete(
                f"/v1/agents/{agent_id}/archival-memory/{p['id']}"
            )
            if del_resp.is_success:
                deleted += 1
        return deleted > 0
    except Exception as exc:
        log.warning("delete(%s, %s) Letta error: %s", agent_role, key, exc)
        probe.mark_unavailable()
        return fallback.delete(agent_role, key)




async def do_store_profile(
    probe: LettaHealthProbe,
    registry: LettaAgentRegistry,
    agent_role: AgentRole,
    label: str,
    value: str,
) -> bool:
    """Write a profile block (persona/human) to Letta core-memory.
    
    Uses PATCH /v1/agents/{agent_id}/core-memory/blocks/{label}
    """
    try:
        agent_id = await registry.get_agent_id(agent_role)
        http = await probe.get_http()
        resp = await http.patch(
            f"/v1/agents/{agent_id}/core-memory/blocks/{label}",
            json={"value": value},
        )
        resp.raise_for_status()
        log.info("Profile %s saved for %s", label, agent_role)
        return True
    except Exception as exc:
        log.warning("do_store_profile(%s, %s) failed: %s", agent_role, label, exc)
        return False

async def do_store(
    probe: LettaHealthProbe, registry: LettaAgentRegistry,
    fallback: _InContextFallback, agent_role: AgentRole, key: MemoryKey,
    content: str, meta: MemoryEntryMetadata, user_tags: list[str],
) -> MemoryEntry:
    try:
        await do_delete(probe, registry, fallback, agent_role, key)
        agent_id = await registry.get_agent_id(agent_role)
        all_tags = [_GLOBAL_TAG, _role_tag(agent_role), _key_tag(key)] + user_tags
        body     = {"text": _passage_text(key, content), "tags": all_tags}
        http     = await probe.get_http()
        resp     = await http.post(f"/v1/agents/{agent_id}/archival-memory", json=body)
        resp.raise_for_status()
        raw  = resp.json()
        data = raw[0] if isinstance(raw, list) else raw  # 0.16.8 returns list[Passage]
        return MemoryEntry(
            id=EntryId(data.get("id") or str(uuid.uuid4())),
            agent_role=agent_role, key=key, content=content,
            metadata=MemoryEntryMetadata(
                tags=all_tags,
                **{k: v for k, v in meta.items() if k != "tags"},  # type: ignore[misc]
            ),
            created_at=IsoTimestamp(data.get("created_at") or _now_iso()),
            source="letta",
        )
    except Exception as exc:
        log.warning("store(%s, %s) Letta error: %s — fallback", agent_role, key, exc)
        probe.mark_unavailable()
        return fallback.store(agent_role, key, content, meta)
