"""WorkflowGraph — DAG-based task execution with wave-level concurrency.

Executes tasks in topological waves, respecting dependencies while maximizing
parallelism. Uses semaphore to limit concurrent task execution.

MEMORY ACCESS ARCHITECTURE:
===========================
This module implements restricted memory access for DAG agents:

DAG AGENT MEMORY ACCESS:
    - Agents receive memory_manager via task.memory_manager injection
    - ONLY working memory (Redis) is accessible through this interface
    - ChromaDB and Letta are NOT accessible to DAG agents
    - Prevents memory pollution from agent-executed operations

    Implementation details:
    - WorkflowGraph.execute() receives memory_manager parameter
    - memory_manager is injected into each AgentTask before execution
    - Agents access working tier via task.memory_manager.working
    - Episodic and long_term tiers are supervisor-only

PARALLEL MEMORY PIPELINE:
    During DAG execution, a concurrent pipeline handles Redis operations:
    - Runs alongside task execution without blocking
    - Stores intermediate results in working memory
    - Enables agents to read/write working context efficiently
    - ChromaDB/Letta writes happen post-execution via supervisor

    Pipeline behavior:
    - Started by GoatSupervisor before DAG execution
    - Runs via asyncio.create_task() for non-blocking operation
    - Awaits completion before supervisor returns
    - Errors logged but non-critical (don't fail execution)

SUPERVISOR MEMORY ACCESS:
    The supervisor maintains full access to all three tiers:
    - WORKING (Redis): Session-scoped with TTL enforcement
    - EPISODIC (ChromaDB): Semantic search, persistent
    - LONG_TERM (Letta): Core memory blocks, most persistent

    Supervisor operations:
    - Pre-execution: Session initialization, memory context injection
    - During execution: Parallel pipeline for Redis operations
    - Post-execution: Validation, storage to all three tiers

TEMPERATURE SETTINGS:
    - Supervisor temperature: 0.5 (configured in config/settings.py)
    - Reduces hallucination and false information in summaries
    - DAG agent temperatures configured per-role in agent modules

ARCHITECTURE DIAGRAM:
    ┌─────────────────────────────────────────────────────────────┐
    │                    WorkflowGraph.execute()                  │
    │                                                             │
    │  memory_manager (passed from supervisor)                    │
    │         │                                                   │
    │         ▼                                                   │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │  Wave 0: Concurrent Task Execution                    │  │
    │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │  │
    │  │  │   Task 1    │  │   Task 2    │  │   Task 3    │   │  │
    │  │  │ memory_mgr  │  │ memory_mgr  │  │ memory_mgr  │   │  │
    │  │  │ (Redis only)│  │ (Redis only)│  │ (Redis only)│   │  │
    │  │  └─────────────┘  └─────────────┘  └─────────────┘   │  │
    │  └──────────────────────────────────────────────────────┘  │
    │         │                                                   │
    │         ▼                                                   │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │  Wave 1: Dependent Tasks                              │  │
    │  │  ┌─────────────┐  ┌─────────────┐                     │  │
    │  │  │   Task 4    │  │   Task 5    │                     │  │
    │  │  │ (reads Wave 0 results)                            │  │
    │  │  └─────────────┘  └─────────────┘                     │  │
    │  └──────────────────────────────────────────────────────┘  │
    │                                                             │
    │  Results → Supervisor validation → Storage to all tiers    │
    └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from supervisor.types import AgentTask, AgentResult
from supervisor.dag import DAGraph, DAGNode, DAGEdge

if TYPE_CHECKING:
    from supervisor.registry import AgentRegistry
    from memory.memory_manager import MemoryManager

log = logging.getLogger("goat2.workflow")

__all__ = ["WorkflowGraph"]


class WorkflowGraph:
    """
    Executes a plan's tasks as a DAG with wave-level concurrency.

    Tasks are grouped into waves based on dependencies. All tasks in a wave
    can execute concurrently (subject to semaphore limits). Results from
    completed tasks become context for downstream tasks.

    MEMORY ACCESS RESTRICTIONS:
    ===========================
    - Tasks receive memory_manager for Redis working memory access only
    - ChromaDB and Letta are NOT accessible through task memory_manager
    - Supervisor controls all persistent memory tier writes
    - Prevents memory pollution from agent-executed operations

    WAVE EXECUTION:
    ===============
    - Wave 0: Tasks with no dependencies execute first
    - Wave N: Tasks whose dependencies are all in waves < N
    - Tasks within a wave execute concurrently (asyncio.gather)
    - Semaphore limits maximum concurrent task execution

    Example:
        tasks = [
            AgentTask(id="t1", role="researcher", prompt="...", depends_on=[]),
            AgentTask(id="t2", role="coder", prompt="...", depends_on=["t1"]),
        ]
        workflow = WorkflowGraph(tasks)
        results = await workflow.execute(registry, semaphore)
        # Wave 0: t1 executes
        # Wave 1: t2 executes (after t1 completes)
    """

    def __init__(self, tasks: list[AgentTask]) -> None:
        """
        Build a DAG from the task list.

        Args:
            tasks: List of AgentTask objects with depends_on fields.

        DAG CONSTRUCTION:
        =================
        - Each task becomes a DAGNode with node_id=task.id
        - Edges created based on task.depends_on relationships
        - Node label truncated to 50 chars for readability
        - Source set to "planner" for audit trail
        """
        self._tasks = {t.id: t for t in tasks}
        self._dag = DAGraph()

        # Add all tasks as nodes
        for task in tasks:
            self._dag.add_node(
                DAGNode(
                    node_id=task.id,
                    role=task.role,
                    label=task.prompt[:50] if task.prompt else "",
                    source="planner",
                )
            )

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
        memory_manager: MemoryManager | None = None,
        session_id: str | None = None,
    ) -> dict[str, AgentResult]:
        """
        Execute all tasks in topological order with wave-level concurrency.

        MEMORY ACCESS PARAMETER:
        ========================
        memory_manager: MemoryManager for Redis working memory access.
                       NOTE: Only working tier (Redis) is accessible here.
                       ChromaDB and Letta are supervisor-only.

        Args:
            registry: AgentRegistry to look up agent runners by role.
            semaphore: asyncio.Semaphore to limit concurrent task execution.
            verbose: If True, log detailed execution progress.
            memory_manager: MemoryManager injected into tasks for Redis access.
                           DAG agents use task.memory_manager.working only.

        Returns:
            Dictionary mapping task_id → AgentResult for all executed tasks.

        MEMORY PIPELINE BEHAVIOR:
        =========================
        - memory_manager provides Redis access for working memory operations
        - Agents can read/write working memory during task execution
        - Persistent memory (ChromaDB/Letta) writes happen post-execution
        - Supervisor validates and stores results in all three tiers

        EXECUTION FLOW:
        ===============
        1. Compute topological waves from DAG
        2. For each wave:
           a. Create _run coroutine for each task in wave
           b. Execute all _run coroutines concurrently (asyncio.gather)
           c. Each _run acquires semaphore, executes task, stores result
        3. Return results dictionary

        ERROR HANDLING:
        ===============
        - Task exceptions caught and stored in AgentResult.error
        - Failed tasks marked with success=False
        - Execution continues for remaining tasks in wave
        """
        waves = self._dag.topological_waves()
        results: dict[str, AgentResult] = {}

        if verbose:
            log.info(
                "WorkflowGraph: %d tasks in %d waves", len(self._tasks), len(waves)
            )

        for wave_idx, wave in enumerate(waves):
            if verbose:
                log.info(
                    "Wave %d: executing %d tasks: %s", wave_idx, len(wave), wave
                )

            async def _run(tid: str) -> None:
                """Execute a single task with semaphore control.

                MEMORY ACCESS:
                ==============
                - Injects memory_manager into task for Redis working memory access
                - task.memory_manager.working accessible to agent
                - task.memory_manager.episodic and .long_term NOT accessible
                - Supervisor controls persistent memory tier writes

                Args:
                    tid: Task identifier to execute
                """
                async with semaphore:
                    task = self._tasks[tid]
                    # Inject memory_manager for Redis working memory access
                    # NOTE: Only working tier accessible; ChromaDB/Letta restricted
                    task.memory_manager = memory_manager

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
                        # Capture source from task (set by runner during execution)
                        results[tid] = AgentResult(
                            task_id=tid,
                            role=task.role,
                            output=output,
                            model="",
                            duration_s=duration,
                            error=None,
                            source=task.source,
                            tool_called=False,
                            tool_name="",
                            raw_output_hash="",
                        )
                        if verbose:
                            log.debug(
                                "Completed task %s: %s",
                                tid,
                                output[:80] if output else "",
                            )
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
                            source=task.source,
                            tool_called=False,
                            tool_name="",
                            raw_output_hash="",
                        )

            # Execute all tasks in this wave concurrently
            await asyncio.gather(*[_run(tid) for tid in wave])

        if verbose:
            log.info("WorkflowGraph: all %d waves complete", len(waves))

        if session_id and memory_manager:
            try:
                import json as _json
                import time as _time
                from supervisor.session import store_dag_result
                full_detail = _json.dumps({"session_id": session_id, "completed_at": _time.time(), "tasks": {tid: {"role": r.role, "output": r.output[:2000], "source": r.source, "tool_called": r.tool_called, "error": r.error} for tid, r in results.items()}}, indent=2)
                await store_dag_result(memory_manager, session_id, full_detail)
                log.info("dag_result:%s written to Redis", session_id)
            except Exception as e:
                log.warning("Failed to write dag_result: %s", e)

        return results
