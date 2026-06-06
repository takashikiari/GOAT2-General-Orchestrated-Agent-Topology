from __future__ import annotations

import logging

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
        hits = await task.memory_manager.recall(task.role, task.prompt[:200], limit=5)
        if hits:
            task.source = "memory"
            return "\n\n".join(h.content for h in hits)

    # Tier 2: direct Letta HTTP (backward-compat — no memory_manager)
    letta = settings.letta
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as http:
            health = await http.get(f"{letta.base_url}/v1/health", headers=letta.headers)
            if health.status_code == 200:
                search = await http.get(
                    f"{letta.base_url}/v1/archival-memory/search",
                    params={"query": task.prompt[:200], "limit": 5},
                    headers=letta.headers,
                )
                if search.status_code == 200:
                    memories = search.json().get("memories", [])
                    if memories:
                        log.debug("Letta returned %d memory blocks", len(memories))
                        task.source = "memory"
                        return "\n\n".join(m.get("text", "") for m in memories)
    except Exception as exc:
        log.debug("Letta unavailable (%s) — falling through to no-memory error", exc)

    # Tier 3: no memory available — return error, never generate fake memory
    log.warning("_run_memory: no real memory found for role=%s prompt=%.60s", task.role, task.prompt)
    task.source = "generated"  # triggers dag_validator UNVERIFIED for this node
    return "ERROR: no memory results from any tier; memory lookup failed"
