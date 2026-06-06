from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Final

from config.settings import settings
from supervisor.types import AgentTask, AgentResult, TaskStatus
from supervisor.registry import AgentRegistry
from supervisor.llm_utils import _model_label

_SOURCE_TOOL: Final[dict[str, str]] = {
    "net": "web_search", "file": "file_read",
    "memory": "memory_search", "generated": "",
}

log = logging.getLogger("goat2.supervisor")

__all__ = ["WorkflowGraph"]


class WorkflowGraph:
    """
    Executes an AgentTask DAG in topological waves via Kahn's algorithm.
    Tasks in the same wave run concurrently, bounded by a shared semaphore.
    """

    def __init__(self, tasks: list[AgentTask]) -> None:
        self.tasks: dict[str, AgentTask] = {t.id: t for t in tasks}

    def topological_waves(self) -> list[list[str]]:
        """Group task IDs into parallel waves; raises ValueError on cycles or unknown deps."""
        in_degree: dict[str, int] = {tid: 0 for tid in self.tasks}
        dependents: dict[str, list[str]] = {tid: [] for tid in self.tasks}
        for task in self.tasks.values():
            for dep in task.depends_on:
                if dep not in self.tasks:
                    raise ValueError(f"Task '{task.id}' depends on unknown task '{dep}'")
                in_degree[task.id] += 1
                dependents[dep].append(task.id)
        waves: list[list[str]] = []
        ready = [tid for tid, deg in in_degree.items() if deg == 0]
        while ready:
            waves.append(list(ready))
            nxt: list[str] = []
            for tid in ready:
                for child in dependents[tid]:
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        nxt.append(child)
            ready = nxt
        if sum(len(w) for w in waves) != len(self.tasks):
            raise ValueError("Workflow graph contains a cycle.")
        return waves

    def validate(self) -> list[str]:
        """
        Run structural validation on the task DAG before execution.

        Returns a list of warning/error messages. An empty list means the
        DAG is structurally valid. Checks:

        - At least one task exists.
        - No self-loops (task depends on itself).
        - All dependency references point to existing tasks.
        - Graph is acyclic (topological sort succeeds).
        - Every task has a non-empty role.
        - Every task has a source label (for audit trail).
        """
        issues: list[str] = []

        if not self.tasks:
            issues.append("Workflow is empty — no tasks defined.")

        for tid, task in self.tasks.items():
            if not task.role.strip():
                issues.append(f"Task '{tid}' has an empty role.")
            if not task.source.strip():
                issues.append(f"Task '{tid}' is missing a 'source' label for audit.")
            for dep in task.depends_on:
                if dep == tid:
                    issues.append(f"Self-loop detected: task '{tid}' depends on itself.")
                if dep not in self.tasks:
                    issues.append(f"Task '{tid}' depends on unknown task '{dep}'.")

        try:
            self.topological_waves()
        except ValueError as exc:
            issues.append(str(exc))

        return issues

    async def execute(
        self, registry: AgentRegistry, semaphore: asyncio.Semaphore, verbose: bool = False,
    ) -> dict[str, AgentResult]:
        """Run all waves sequentially; tasks within each wave run concurrently."""
        results: dict[str, AgentResult] = {}

        async def _run(task_id: str) -> None:
            task = self.tasks[task_id]
            dep_results = {dep: results[dep] for dep in task.depends_on}
            task.status = TaskStatus.RUNNING
            t0 = time.monotonic()
            try:
                output = await asyncio.wait_for(
                    registry.get(task.role)(task, dep_results),
                    timeout=settings.supervisor.turn_timeout,
                )
                task.status = TaskStatus.DONE
                results[task_id] = AgentResult(
                    task_id=task_id, role=task.role, output=output,
                    model=_model_label(task.role), duration_s=time.monotonic() - t0,
                    source=task.source,
                    tool_called=task.source != "generated",
                    tool_name=_SOURCE_TOOL.get(task.source, ""),
                    raw_output_hash=hashlib.sha256(output.encode()).hexdigest()[:16],
                )
                if verbose:
                    log.info(
                        "  \u2713 [%s] %s (%.1fs)  source=%s tool_called=%s",
                        task_id, task.role, results[task_id].duration_s,
                        task.source, task.source != "generated",
                    )
            except Exception as exc:
                task.status = TaskStatus.FAILED
                results[task_id] = AgentResult(
                    task_id=task_id, role=task.role, output="",
                    model=_model_label(task.role), duration_s=time.monotonic() - t0,
                    error=str(exc), source=task.source,
                    tool_called=False, tool_name="", raw_output_hash="",
                )
                log.error(
                    "  \u2717 [%s] %s FAILED: %s  source=%s",
                    task_id, task.role, exc, task.source,
                )

        async def _guarded(task_id: str) -> None:
            async with semaphore:
                await _run(task_id)

        # Validate before execution — missing source labels are errors, not warnings
        issues = self.validate()
        src_issues = [i for i in issues if "source" in i.lower()]
        other_issues = [i for i in issues if "source" not in i.lower()]
        if src_issues:
            raise ValueError(f"DAG blocked — missing source label: {src_issues[0]}")
        if other_issues:
            log.warning("Workflow validation issues: %s", "; ".join(other_issues))

        for wave_idx, wave in enumerate(self.topological_waves()):
            if verbose:
                log.info("Wave %d \u2014 spawning: %s", wave_idx + 1, wave)
            await asyncio.gather(*[_guarded(tid) for tid in wave])
        return results
