"""WorkflowGraph — executes AgentTask DAG in topological waves via Kahn's algorithm.

Tasks in the same wave run concurrently, bounded by a shared semaphore.
Populates AgentResult with source provenance tracking for validation.
"""
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
    Each task's output becomes an AgentResult with source provenance tracking.
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
        if len(waves) != len(self.tasks):
            raise ValueError("Cycle detected in task DAG")
        return waves

    async def execute(
        self,
        registry: AgentRegistry,
        semaphore: asyncio.Semaphore,
        verbose: bool = False,
    ) -> dict[str, AgentResult]:
        """Execute all tasks in topological order; returns dict of AgentResult by task ID."""
        results: dict[str, AgentResult] = {}
        waves = self.topological_waves()
        for wave in waves:
            async def _run(tid: str) -> None:
                async with semaphore:
                    task = self.tasks[tid]
                    task.status = TaskStatus.RUNNING
                    t0 = time.monotonic()
                    dep_results = {d: results[d] for d in task.depends_on if d in results}
                    try:
                        runner = registry.get(task.role)
                        output = await runner(task, dep_results)
                        task.status = TaskStatus.DONE
                        duration = time.monotonic() - t0
                        source = task.source or "generated"
                        tool_name = _SOURCE_TOOL.get(source, "")
                        output_hash = hashlib.sha256(output.encode()).hexdigest()[:16]
                        results[tid] = AgentResult(
                            task_id=tid, role=task.role, output=output,
                            model=_model_label(task.role), duration_s=duration,
                            source=source, tool_called=source != "generated",
                            tool_name=tool_name, raw_output_hash=output_hash,
                        )
                        if verbose:
                            log.info("Task %s (%s) done in %.1fs — source=%s",
                                     tid, task.role, duration, source)
                    except Exception as exc:
                        task.status = TaskStatus.FAILED
                        duration = time.monotonic() - t0
                        log.error("Task %s (%s) failed: %s", tid, task.role, exc)
                        results[tid] = AgentResult(
                            task_id=tid, role=task.role, output="",
                            model=_model_label(task.role), duration_s=duration,
                            error=str(exc), source="generated", tool_called=False,
                            tool_name="", raw_output_hash="",
                        )
            await asyncio.gather(*[_run(tid) for tid in wave])
        return results
