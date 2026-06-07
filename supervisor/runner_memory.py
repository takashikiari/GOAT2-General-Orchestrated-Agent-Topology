"""Memory runner for GOAT 2.0 DAG tasks.

Searches three memory tiers in priority order. Sets task.source based on
which tier responds. Never generates fake memory content.
"""
from __future__ import annotations

import logging

from config.roles import SESSION_ROLE
from config.settings import settings
from supervisor.types import AgentTask, AgentResult

log = logging.getLogger("goat2.supervisor")


async def _run_memory(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
    """Retrieve relevant prior memories; sets task.source based on which tier responds.

    Tier 1: memory_manager (source=memory). Tier 2: direct Letta HTTP (source=memory).
    Tier 3: no memory found — returns error string and source=generated (triggers UNVERIFIED).
    """
    # Tier 1: memory_manager injected by GoatSupervisor (preferred path)
    if task.memory_manager is not None:
        hits = await task.memory_manager.recall(SESSION_ROLE, task.prompt[:200], limit=5)
        if hits:
            task.source = "memory"
            return "\n\n".join(h.content for h in hits)

    # Tier 2: direct Letta HTTP (backward-compat — no memory_manager)
    # Path: GET /v1/agents/{agent_id}/archival-memory  (agent-scoped, never global)
    letta = settings.letta
    try:
        import httpx
        async with httpx.AsyncClient(
            base_url=letta.base_url, headers=letta.headers, timeout=8
        ) as http:
            health = await http.get("/v1/health/")
            if health.status_code != 200:
                raise RuntimeError(f"Letta health={health.status_code}")
            # Resolve memory agent ID dynamically — never hardcode
            ar = await http.get("/v1/agents/", params={"name": "goat2-memory", "limit": 5})
            ar.raise_for_status()
            raw = ar.json()
            all_agents = raw if isinstance(raw, list) else raw.get("agents", [])
            # Filter by exact name — Letta may ignore the name param and return all agents
            matched = next((a for a in all_agents if a.get("name") == "goat2-memory"), None)
            if matched is None:
                raise RuntimeError("Letta agent 'goat2-memory' not found")
            agent_id = matched["id"]
            kw = max(
                (w.strip("?.,!;:'\"" for w in task.prompt[:200].split() if len(w) > 3),
                key=len, default=task.prompt[:30],
            )
            log.debug("Letta Tier2: agent=%s kw=%r", agent_id, kw)
            sr = await http.get(
                f"/v1/agents/{agent_id}/archival-memory",
                params={"search": kw, "limit": 5},
            )
            log.debug("Letta Tier2: status=%d body=%.300s", sr.status_code, sr.text[:300])
            sr.raise_for_status()
            data = sr.json()
            passages = data if isinstance(data, list) else (
                data.get("results") or data.get("passages") or []
            )
            texts = [p.get("text") or p.get("content") or "" for p in passages]
            texts = [t for t in texts if t.strip()]
            if texts:
                log.debug("Letta Tier2: found %d passages", len(texts))
                task.source = "memory"
                return "\n\n".join(texts)
    except Exception as exc:
        log.debug("Letta Tier2 unavailable (%s) — falling through", exc)

    # Tier 3: no memory available — return error, never generate fake memory
    log.warning("_run_memory: no real memory found for role=%s prompt=%.60s", task.role, task.prompt)
    task.source = "generated"  # triggers dag_validator UNVERIFIED for this node
    return "ERROR: no memory results from any tier; memory lookup failed"
