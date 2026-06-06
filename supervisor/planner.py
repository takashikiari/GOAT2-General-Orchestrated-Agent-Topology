"""Task decomposition engine for GOAT 2.0 — breaks intent into minimal subtasks."""
from __future__ import annotations

import logging
from typing import Final

from config.settings import Provider, settings
from supervisor.types import AgentTask, AgentResult, Plan
from supervisor.llm_utils import _call_llm, _extract_json, _format_dep_context

log = logging.getLogger("goat2.supervisor")

PLANNER_SYSTEM: Final[str] = (
    "You are a task decomposition engine for a multi-agent system. "
    "Break the user's intent into a minimal, focused set of subtasks. "
    "Return ONLY valid JSON — no markdown, no prose — matching this schema:\n"
    '{"tasks": [{"id": "snake_case_id", '
    '"role": "researcher|coder|critic|summarizer|tool_caller|memory", '
    '"prompt": "specific instructions for this agent", '
    '"depends_on": ["other_task_id"]}]}\n'
    "Rules:\n"
    "  - IDs must be unique snake_case strings\n"
    "  - Use depends_on to model data flow (downstream tasks receive upstream outputs)\n"
    "  - Always include a final summarizer task that depends on all other tasks\n"
    "  - Keep tasks atomic and role-appropriate\n"
    "  - 2–8 tasks total\n"
    "  - Decompose ONLY the current user intent. Ignore previous assistant responses.\n"
    "  - Do NOT use prior DAG results (web search, file reads) as input for new tasks."
)


async def decompose_plan(intent: str) -> Plan:
    """Call the supervisor model to decompose intent into an AgentTask DAG."""
    spec = settings.supervisor.model
    raw = await _call_llm(
        spec,
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user",   "content": f"Decompose this intent into tasks:\n\n{intent}"},
        ],
        json_mode=(spec.provider == Provider.OPENAI),
    )
    try:
        data  = _extract_json(raw)
        tasks = [
            AgentTask(
                id=t["id"], role=t["role"], prompt=t["prompt"],
                depends_on=t.get("depends_on", []),
            )
            for t in data["tasks"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Planner output malformed (%s) — using fallback plan", exc)
        tasks = [
            AgentTask(id="research_1",  role="researcher", prompt=intent),
            AgentTask(id="summarize_1", role="summarizer", prompt=intent,
                      depends_on=["research_1"]),
        ]
    return Plan(tasks=tasks)


async def _run_planner(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
    """Built-in planner runner — registered for completeness; supervisor uses decompose_plan directly."""
    task.source = "generated"
    spec    = settings.agents.get("planner")
    context = _format_dep_context(dep_results)
    return await _call_llm(
        spec,
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user",   "content": f"{context}\n\nIntent: {task.prompt}".strip()},
        ],
        json_mode=True,
    )
