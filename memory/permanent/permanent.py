"""memory.permanent.permanent — long-term facts (core-memory) and archived history (Letta archival memory)."""
from __future__ import annotations

import json

from memory.config import PERMANENT_AGENT_NAME, PERMANENT_LETTA_MODEL, PERMANENT_LETTA_URL
from utils.logging.setup import get_logger

log = get_logger(__name__)

_FACTS_LABEL = "facts"


class PermanentMemory:
    """Letta-backed permanent store: facts in core-memory block 'facts', history in archival memory."""

    def __init__(self) -> None:
        self._agent_id: str | None = None
        self._http = None

    def _get_http(self):
        if self._http is None:
            import httpx  # lazy — avoids import-time network activity
            self._http = httpx.AsyncClient(base_url=PERMANENT_LETTA_URL, timeout=10.0)
        return self._http

    async def _resolve_agent_id(self) -> str:
        if self._agent_id is not None:
            return self._agent_id
        http = self._get_http()
        resp = await http.get("/v1/agents/", params={"name": PERMANENT_AGENT_NAME})
        resp.raise_for_status()
        agents = resp.json()
        if agents:
            self._agent_id = agents[0]["id"]
            log.debug("PermanentMemory: found agent %s", self._agent_id)
        else:
            r = await http.post("/v1/agents/", json={
                "name": PERMANENT_AGENT_NAME,
                "model": PERMANENT_LETTA_MODEL,
                "memory_blocks": [{"label": _FACTS_LABEL, "value": "{}"}],
            })
            r.raise_for_status()
            self._agent_id = r.json()["id"]
            log.info("PermanentMemory: created agent %s", self._agent_id)
        return self._agent_id

    async def _get_facts(self) -> dict[str, str]:
        agent_id = await self._resolve_agent_id()
        resp = await self._get_http().get(
            f"/v1/agents/{agent_id}/core-memory/blocks/{_FACTS_LABEL}"
        )
        resp.raise_for_status()
        return json.loads(resp.json()["value"])

    async def _save_facts(self, facts: dict[str, str]) -> None:
        agent_id = await self._resolve_agent_id()
        resp = await self._get_http().patch(
            f"/v1/agents/{agent_id}/core-memory/blocks/{_FACTS_LABEL}",
            json={"value": json.dumps(facts)},
        )
        resp.raise_for_status()

    async def store_fact(self, key: str, value: str) -> None:
        """Store or update a named fact (e.g. key='user_name', value='Takashi')."""
        facts = await self._get_facts()
        facts[key] = value
        await self._save_facts(facts)
        log.debug("PermanentMemory: stored fact key=%s", key)

    async def get_fact(self, key: str) -> str | None:
        """Retrieve a single named fact, or None if not set."""
        return (await self._get_facts()).get(key)

    async def get_all_facts(self) -> dict[str, str]:
        """Return all stored facts as a dict."""
        return await self._get_facts()

    async def archive_entries(self, entries: list[dict]) -> None:
        """Batch-store promoted episodic entries in Letta's archival memory."""
        agent_id = await self._resolve_agent_id()
        for e in entries:
            ts = float(e["metadata"].get("timestamp", 0))
            text = f"[{e['metadata'].get('role', '?')} ts={ts:.0f}] {e['content']}"
            (await self._get_http().post(
                f"/v1/agents/{agent_id}/archival-memory", json={"text": text}
            )).raise_for_status()
        log.info("PermanentMemory: archived %d entries", len(entries))
