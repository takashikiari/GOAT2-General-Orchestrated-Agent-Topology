"""WorkflowGraph — DAG-based task execution with wave-level concurrency.

Executes tasks in topological waves, respecting dependencies while maximizing
parallelism. Uses semaphore to limit concurrent task execution.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from supervisor.types import AgentTask, AgentResult
from supervisor.dag import DAGraph, DAGNode, DAGEdge

if TYPE_CHECKING:
    from supervisor.registry import AgentRegistry

log = logging.getLogger("goat2.workflow")

__all__ = ["WorkflowGraph"]


class WorkflowGraph:
    """
    Executes a plan's tasks as a DAG with wave-level concurrency.

    Tasks are grouped into waves based on dependencies. All tasks in a wave
    can execute concurrently (subject to semaphore limits). Results from
    completed tasks become context for downstream tasks.
    """

    def __init__(self, tasks: list[AgentTask]) -> None:
        """
        Build a DAG from the task list.

        Args:
            tasks: List of AgentTask objects with depends_on fields.
        """
        self._tasks = {t.id: t for t in tasks}
        self._dag = DAGraph()

        # Add all tasks as nodes
        for task in tasks:
            self._dag.add_node(DAGNode(
                node_id=task.id,
                role=task.role,
                label=task.prompt[:50] if task.prompt else "",
                source="planner",
            ))

        # Add edges based on depends_on
        for task in tasks:
            for dep_id in task.depends_on:
                if dep_id in self._tasks:
                    self._dag.add_edge(DAGEdge(source=dep_id, target=task.id))

    async def execute(
        self,
        registry: AgentRegistry,
        semaphore: asyncio.Semaphore,
        *,
        verbose: bool = False,
    ) -> dict[str, AgentResult]:
        """
        Execute all tasks in topological order with wave-level concurrency.

        Args:
            registry: AgentRegistry to look up agent runners by role.
            semaphore: asyncio.Semaphore to limit concurrent task execution.
            verbose: If True, log detailed execution progress.

        Returns:
            Dictionary mapping task_id → AgentResult for all executed tasks.
        """
        waves = self._dag.topological_waves()
        results: dict[str, AgentResult] = {}

        if verbose:
            log.info("WorkflowGraph: %d tasks in %d waves", len(self._tasks), len(waves))

        for wave_idx, wave in enumerate(waves):
            if verbose:
                log.info("Wave %d: executing %d tasks: %s", wave_idx, len(wave), wave)

            async def _run(tid: str) -> None:
                """Execute a single task with semaphore control."""
                import time
                async with semaphore:
                    task = self._tasks[tid]
                    # Build context from completed dependencies
                    context = {
                        dep_id: results[dep_id]
                        for dep_id in task.depends_on
                        if dep_id in results
                    }

                    if verbose:
                        log.debug("Starting task %s (role=%s)", tid, task.role)

                    t_start = time.monotonic()
                    try:
                        runner = registry.get(task.role)
                        output = await runner(task, context)
                        duration = time.monotonic() - t_start
                        results[tid] = AgentResult(
                            task_id=tid,
                            role=task.role,
                            output=output,
                            model="",
                            duration_s=duration,
                            error=None,
                            source="",
                            tool_called=False,
                            tool_name="",
                            raw_output_hash="",
                        )
                        if verbose:
                            log.debug("Completed task %s: %s", tid, output[:80] if output else "")
                    except Exception as e:
                        duration = time.monotonic() - t_start
                        log.exception("Task %s failed", tid)
                        results[tid] = AgentResult(
                            task_id=tid,
                            role=task.role,
                            output="",
                            model="",
                            duration_s=duration,
                            error=str(e),
                            source="",
                            tool_called=False,
                            tool_name="",
                            raw_output_hash="",
                        )

            # Execute all tasks in this wave concurrently
            await asyncio.gather(*[_run(tid) for tid in wave])

        if verbose:
            log.info("WorkflowGraph: all %d waves complete", len(waves))

        return results
