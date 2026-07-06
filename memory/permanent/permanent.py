"""memory.permanent.permanent — long-term facts in the Letta L1 core-memory 'facts' block."""
from __future__ import annotations

import json

from memory.config import PERMANENT_AGENT_NAME, PERMANENT_LETTA_MODEL, PERMANENT_LETTA_URL
from utils.logging.setup import get_logger

log = get_logger(__name__)

_FACTS_LABEL = "facts"
_IDENTITY_LABEL = "identity"


class PermanentMemory:
    """Letta-backed permanent store: facts in the core-memory block 'facts'."""

    def __init__(self) -> None:
        self._agent_id: str | None = None
        self._http = None

    def _get_http(self):
        if self._http is None:
            import httpx  # lazy — avoids import-time network activity
            self._http = httpx.AsyncClient(
                base_url=PERMANENT_LETTA_URL, timeout=10.0, follow_redirects=True
            )
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
                "memory_blocks": [
                    {"label": _FACTS_LABEL, "value": "{}"},
                    {"label": _IDENTITY_LABEL, "value": ""},
                ],
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

    async def delete_fact(self, key: str) -> bool:
        """Remove a fact by key. Returns True if it existed, False if not found."""
        facts = await self._get_facts()
        if key not in facts:
            return False
        del facts[key]
        await self._save_facts(facts)
        log.debug("PermanentMemory: deleted fact key=%s", key)
        return True

    async def get_identity_override(self) -> str | None:
        """Return the Letta identity override, or None if unset / unavailable.

        Never raises — a 404 (block absent on old agents) or any network error
        returns None, which signals the caller to fall back to the config prompt.
        """
        try:
            agent_id = await self._resolve_agent_id()
            resp = await self._get_http().get(
                f"/v1/agents/{agent_id}/core-memory/blocks/{_IDENTITY_LABEL}"
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            value = resp.json().get("value", "").strip()
            return value or None
        except Exception:  # noqa: BLE001 — identity is best-effort
            return None

    async def set_identity_override(self, text: str) -> None:
        """Write (or create) the identity block in Letta.

        Tries PATCH first (update existing block). On 404 the block doesn't
        exist on this agent yet — use the two-step Letta flow: create a
        standalone block via POST /v1/blocks, then attach it to the agent via
        POST /v1/agents/{id}/core-memory/blocks/{block_id}.
        Raises on any other HTTP error.
        """
        agent_id = await self._resolve_agent_id()
        http = self._get_http()
        resp = await http.patch(
            f"/v1/agents/{agent_id}/core-memory/blocks/{_IDENTITY_LABEL}",
            json={"value": text},
        )
        if resp.status_code == 404:
            # Step 1: create standalone block
            r = await http.post(
                "/v1/blocks",
                json={"label": _IDENTITY_LABEL, "value": text, "template": False},
            )
            r.raise_for_status()
            block_id = r.json()["id"]
            # Step 2: attach to agent
            resp = await http.post(
                f"/v1/agents/{agent_id}/core-memory/blocks/{block_id}",
            )
        resp.raise_for_status()
        log.debug("PermanentMemory: identity override set (%d chars)", len(text))
