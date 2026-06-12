"""Built-in agent runner implementations for GOAT 2.0.

Each runner is an async callable (AgentTask, dep_results, registry) -> str
that sets task.source before returning so the workflow can propagate source
provenance. Tool selection is delegated entirely to each agent class.

SEVERITY OUTPUT (_run_critic):
_run_critic() returns a string starting with
``SEVERITY: PASS|MINOR|MAJOR|CRITICAL`` so WorkflowGraph can decide to
re-execute upstream tasks on critical verdicts.

REGISTRY INJECTION (PHASE 4):
All runner functions require the `registry` parameter and use
``registry.settings.agents.get(role)``.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from supervisor.types import AgentTask, AgentResult

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.pipeline")
__all__ = [
    "_run_researcher",
    "_run_coder",
    "_run_critic",
    "_run_summarizer",
    "_run_tool_caller",
    "_run_memory",
]


async def _run_researcher(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Research agent: delegates to ResearcherAgent (WEB_SEARCH + MEMORY_SEARCH_DAG)."""
    from agents.researcher import ResearcherAgent
    agent = ResearcherAgent(spec=registry.settings.agents.get("researcher"))
    log.debug("_run_researcher: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, dep_results)
    task.source = "net" if agent.spec.tool_calling else "generated"
    return output


async def _run_coder(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Code generation: delegates to CoderAgent (file tools + SHELL + validate_syntax)."""
    from agents.coder import CoderAgent
    agent = CoderAgent(spec=registry.settings.agents.get("coder"))
    log.debug("_run_coder: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, dep_results)
    task.source = "file"
    return output


async def _run_critic(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Critical review: delegates to CriticAgent; prepends SEVERITY: prefix for WorkflowGraph."""
    from agents.critic import CriticAgent
    agent = CriticAgent(spec=registry.settings.agents.get("critic"))
    log.debug("_run_critic: task_id=%s spec=%s", task.id, agent.spec)
    output = await agent.execute(task, dep_results)
    verdict = agent.extract_verdict(output)
    if agent.is_blocking(output):
        severity = "CRITICAL"
    elif verdict == "ACCEPT":
        severity = "PASS"
    elif verdict == "REVISE":
        severity = "MAJOR"
    else:
        severity = "MINOR"
    task.source = "generated"
    log.debug("_run_critic: task_id=%s severity=%s verdict=%s", task.id, severity, verdict)
    return f"SEVERITY: {severity}\n{output}"


async def _run_summarizer(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Synthesis: delegates to SummarizerAgent; skips LLM when all upstream outputs empty."""
    if dep_results and all(not (r.output or "").strip() for r in dep_results.values()):
        task.source = "generated"
        return "Not available. Upstream tasks returned no output."
    from agents.summarizer import SummarizerAgent
    agent = SummarizerAgent(spec=registry.settings.agents.get("summarizer"))
    log.debug("_run_summarizer: task_id=%s spec=%s", task.id, agent.spec)
    output = await agent.execute(task, dep_results)
    task.source = "generated"
    return output


async def _run_tool_caller(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Tool orchestration: delegates to ToolCallerAgent (file + DAG memory tools)."""
    from agents.tool_caller import ToolCallerAgent
    agent = ToolCallerAgent(spec=registry.settings.agents.get("tool_caller"))
    log.debug("_run_tool_caller: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, dep_results)
    task.source = "file"
    return output


async def _run_memory(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Working-memory persistence: delegates to MemoryAgent (reuses tool_caller spec)."""
    from agents.memory_agent import MemoryAgent
    agent = MemoryAgent(spec=registry.settings.agents.get("tool_caller"))
    log.debug("_run_memory: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, dep_results)
    task.source = "memory"
    return output
