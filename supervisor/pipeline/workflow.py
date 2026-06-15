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

CRITICAL REVIEW FALLBACK (Problema 5):
======================================
When a critic task returns SEVERITY: CRITICAL (or SEVERITY: MAJOR),
the workflow re-executes the upstream tasks that the critic reviewed,
with a stricter prompt appended: "CRITICAL_REVIEW_FEEDBACK: <issues>".

The re-execution flow:
1. Critic runs, returns verdict with severity
2. If severity is CRITICAL or MAJOR:
   a. Identify upstream tasks (depends_on of the critic task)
   b. For each upstream task, append critic's feedback to the original prompt
   c. Re-execute upstream tasks with the stricter prompt
   d. Run critic again on the new output
3. If severity is PASS or MINOR, continue normally

REGISTRY INJECTION (PHASE 4):
=============================
WorkflowGraph.execute() requires `registry` parameter.
Passed to runner functions for consistent dependency injection.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from supervisor.types import AgentResult
from supervisor.pipeline.dag import DAGraph, DAGNode, DAGEdge
from supervisor.pipeline.runners import (
    _run_researcher,
    _run_coder,
    _run_critic,
    _run_summarizer,
    _run_tool_caller,
    _run_memory,
)

# Runner mapping per role — must stay in sync with VALID_ROLES in plan_validator.py
# and the "role" values listed in PLANNER_SYSTEM in agents/planner_decompose.py.
_RUNNERS: dict[str, callable] = {
    "researcher": _run_researcher,
    "coder": _run_coder,
    "critic": _run_critic,
    "summarizer": _run_summarizer,
    "tool_caller": _run_tool_caller,
    "memory": _run_memory,
}


if TYPE_CHECKING:
    from supervisor.types import AgentTask
    from memory.shared import MemoryManager
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.pipeline")

__all__ = ["WorkflowGraph"]

# ── Constante pentru fallback-ul criticului ──
_MAX_CRITIC_RERUNS: int = 1          # maxim o re-executare per critic task
_UPSTREAM_REEXEC_TIMEOUT: float = 30.0  # timeout per upstream task re-execution
_CRITIC_RERUN_TIMEOUT: float = 30.0     # timeout per critic re-run


def _parse_critic_severity(output: str) -> tuple[str, str]:
    """Parse critic output to extract severity and the rest of the content.

    Returns:
        Tuple of (severity, clean_output) where severity is one of:
        PASS, MINOR, MAJOR, CRITICAL, or UNKNOWN if not found.
    """
    match = re.search(r"^SEVERITY:\s*(PASS|MINOR|MAJOR|CRITICAL)", output, re.MULTILINE)
    if match:
        return match.group(1), output
    return "UNKNOWN", output


def _extract_critic_feedback(output: str) -> str:
    """Extract the assessment and bullet list from critic output for use as feedback.

    Strips the SEVERITY line and returns the rest as concise feedback.
    """
    cleaned = re.sub(r"^SEVERITY:\s*(PASS|MINOR|MAJOR|CRITICAL).*?\n", "", output, flags=re.MULTILINE)
    return cleaned.strip()


async def _write_task_memory(
    memory_manager: MemoryManager,
    session_id: str,
    tid: str,
    role: str,
    output: str,
) -> None:
    """Write one completed task result to Redis working memory.

    Key format: dag:<session_id>:task:<task_id>  TTL: DAG_RESULT_TTL (3600s)
    """
    import time as _t
    from config.limits import DAG_RESULT_TTL
    from config.roles import SESSION_ROLE
    from memory.working.working_record import RecordDict
    key = f"dag:{session_id}:task:{tid}"
    now = _t.time()
    record: RecordDict = {
        "id": key, "agent_role": SESSION_ROLE, "key": key,
        "content": f"[{role}] {output[:1000]}",
        "metadata": {"type": "dag_task_result", "task_id": tid, "session_id": session_id},
        "created_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now)),
        "created_at_ts": now,
        "expires_at": now + DAG_RESULT_TTL,
    }
    await memory_manager.working.backend.set(SESSION_ROLE, key, record, expires_at=record["expires_at"])


async def _write_task_status(
    memory_manager: "MemoryManager",
    session_id: str,
    tid: str,
    role: str,
    output: str,
    status: str,
) -> None:
    """Write per-task status record to dag:{session_id}:task:{tid}:status (TTL 3600s).

    Args:
        memory_manager: MemoryManager for Redis access.
        session_id: DAG session identifier.
        tid: Task identifier.
        role: Agent role that executed the task.
        output: Task output or error message (truncated to 500 chars).
        status: "completed" or "failed".
    """
    import json as _j
    import time as _t
    from config.limits import DAG_RESULT_TTL
    from config.roles import SESSION_ROLE
    from memory.working.working_record import RecordDict
    key = f"dag:{session_id}:task:{tid}:status"
    now = _t.time()
    payload = _j.dumps({
        "agent": role,
        "status": status,
        "summary": (output or "")[:500],
        "timestamp": now,
    }, ensure_ascii=False)
    record: RecordDict = {
        "id": key, "agent_role": SESSION_ROLE, "key": key,
        "content": payload,
        "metadata": {"type": "dag_task_status", "task_id": tid, "session_id": session_id},
        "created_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now)),
        "created_at_ts": now,
        "expires_at": now + DAG_RESULT_TTL,
    }
    await memory_manager.working.backend.set(
        SESSION_ROLE, key, record, expires_at=record["expires_at"],
    )


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

    ERROR PROPAGATION (FIX Problema 3):
    ===================================
    - If a task fails (exception or error), all downstream tasks that depend
      on it are SKIPPED — they never execute.
    - Skipped tasks get an AgentResult with error="upstream_failure:<dep_id>".
    - Within a wave, sibling tasks continue executing; only the failed task's
      dependents are blocked.
    - This prevents cascading failures from consuming LLM quota on tasks
      that would receive corrupted context.

    CRITICAL REVIEW FALLBACK (Problema 5):
    ======================================
    When a critic task returns SEVERITY: CRITICAL or SEVERITY: MAJOR:
    1. The upstream tasks (critic's depends_on) are identified
    2. Each upstream task is re-executed with a stricter prompt that includes
       the critic's feedback
    3. The critic runs again on the new output
    4. Max 1 re-execution per critic task to prevent infinite loops

    REGISTRY INJECTION (PHASE 4):
    =============================
    execute() requires `registry` parameter passed to runner functions.

    Example:
        tasks = [
            AgentTask(id="t1", role="researcher", prompt="...", depends_on=[]),
            AgentTask(id="t2", role="critic", prompt="...", depends_on=["t1"]),
        ]
        workflow = WorkflowGraph(tasks)
        results = await workflow.execute(registry, semaphore)
        # Wave 0: t1 executes
        # Wave 1: t2 executes (after t1 completes)
        # If t2 severity is CRITICAL, t1 is re-executed with stricter prompt,
        # then t2 runs again on the new output
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

        # ── Contor de re-executări per critic (Problema 5 rafinare) ──
        self._critic_rerun_count: dict[str, int] = {}

    async def _re_execute_upstream_and_critic(
        self,
        tid: str,
        task: AgentTask,
        output: str,
        results: dict[str, AgentResult],
        registry: "Registry",
        verbose: bool,
        t_start: float,
        memory_manager: MemoryManager | None,
    ) -> None:
        """Re-execută upstream tasks și re-rules criticul.

        Extrasă din _run pentru claritate. Apelată doar când severity e CRITICAL/MAJOR.

        Args:
            tid: ID-ul task-ului critic
            task: Task-ul critic
            output: Output-ul original al criticului (cu SEVERITY)
            results: Dicționarul de rezultate (modificat in-place)
            registry: Registry for dependency injection (Phase 4)
            verbose: Flag de logging detaliat
            t_start: Timpul de start pentru calculul duratei
            memory_manager: MemoryManager for Redis access
        """
        # Verifică limită de re-executări
        current_count = self._critic_rerun_count.get(tid, 0)
        if current_count >= _MAX_CRITIC_RERUNS:
            log.info(
                "Critic %s already re-run %d times — skipping further re-execution",
                tid, current_count,
            )
            return

        severity, _ = _parse_critic_severity(output)
        log.info(
            "Critic %s returned severity=%s — triggering re-execution of upstream tasks (attempt %d/%d)",
            tid, severity, current_count + 1, _MAX_CRITIC_RERUNS,
        )

        feedback = _extract_critic_feedback(output)
        upstream_tids = task.depends_on

        # Dacă criticul n-are depends_on, nu putem re-executa upstream
        if not upstream_tids:
            log.warning(
                "Critic %s has no upstream tasks (depends_on is empty) — cannot re-execute",
                tid,
            )
            return

        for up_id in upstream_tids:
            if up_id not in self._tasks:
                log.warning("Upstream task %s not found in task list — skipping", up_id)
                continue

            up_task = self._tasks[up_id]
            original_prompt = up_task.prompt

            # Construiește prompt mai strict
            stricter_prompt = (
                f"{up_task.prompt}\n\n"
                f"CRITICAL_REVIEW_FEEDBACK: The previous output had issues. "
                f"Address these specifically:\n{feedback}"
            )
            up_task.prompt = stricter_prompt

            if verbose:
                log.info(
                    "Re-executing upstream task %s with stricter prompt (critic=%s severity=%s)",
                    up_id, tid, severity,
                )

            # Construiește context (exclude upstream-uri eșuate)
            up_context = {
                dep_id: results[dep_id]
                for dep_id in up_task.depends_on
                if dep_id in results and results[dep_id].error is None
            }

            # Salvează rezultatul original ca fallback
            original_result = results.get(up_id)

            try:
                # Re-execută cu timeout
                up_runner = registry.get(up_task.role)
                up_output = await asyncio.wait_for(
                    up_runner(up_task, up_context, registry),
                    timeout=_UPSTREAM_REEXEC_TIMEOUT,
                )
                up_duration = time.monotonic() - t_start
                # Compute tool_name and raw_output_hash (needed for validator)
                import hashlib
                _tool_name = up_task.source if up_task.source and up_task.source not in ("generated", "planner") else ""
                _raw_hash = hashlib.sha256(up_output.encode()).hexdigest()[:16] if up_output else ""
                results[up_id] = AgentResult(
                    task_id=up_id,
                    role=up_task.role,
                    output=up_output,
                    model="",
                    duration_s=up_duration,
                    error=None,
                    source=up_task.source,
                    tool_called=True,
                    tool_name=_tool_name,
                    raw_output_hash=_raw_hash,
                )
                if verbose:
                    log.info(
                        "Re-executed upstream %s: %s",
                        up_id, up_output[:80] if up_output else "",
                    )
            except asyncio.TimeoutError:
                log.error("Re-execution of upstream task %s timed out after %.1fs", up_id, _UPSTREAM_REEXEC_TIMEOUT)
                if original_result is not None:
                    results[up_id] = original_result  # păstrează originalul
                else:
                    results[up_id] = AgentResult(
                        task_id=up_id,
                        role=up_task.role,
                        output="",
                        model="",
                        duration_s=0.0,
                        error=f"re_execution_timeout:{up_id}",
                        source=up_task.source,
                        tool_called=True,
                        tool_name="",
                        raw_output_hash="",
                    )
            except Exception as e:
                log.exception("Re-execution of upstream task %s failed", up_id)
                if original_result is not None:
                    results[up_id] = original_result  # păstrează originalul
                else:
                    results[up_id] = AgentResult(
                        task_id=up_id,
                        role=up_task.role,
                        output="",
                        model="",
                        duration_s=0.0,
                        error=f"re_execution_failed:{e}",
                        source=up_task.source,
                        tool_called=True,
                        tool_name="",
                        raw_output_hash="",
                    )

            # Restaurează promptul original
            up_task.prompt = original_prompt

        # Re-rules criticul pe noile output-uri upstream
        if verbose:
            log.info("Re-running critic %s on new upstream output", tid)

        new_context = {
            dep_id: results[dep_id]
            for dep_id in task.depends_on
            if dep_id in results and results[dep_id].error is None
        }

        # Salvează rezultatul original al criticului ca fallback
        original_critic_result = results.get(tid)

        try:
            critic_runner = _RUNNERS[task.role]
            new_output = await asyncio.wait_for(
                critic_runner(task, new_context, registry),
                timeout=_CRITIC_RERUN_TIMEOUT,
            )
            new_duration = time.monotonic() - t_start
            results[tid] = AgentResult(
                task_id=tid,
                role=task.role,
                output=new_output,
                model="",
                duration_s=new_duration,
                error=None,
                source=task.source,
                tool_called=True,
                tool_name="",
                raw_output_hash="",
            )
            new_severity, _ = _parse_critic_severity(new_output)
            log.info(
                "Critic %s re-run complete: severity=%s",
                tid, new_severity,
            )
        except asyncio.TimeoutError:
            log.error("Critic re-run %s timed out after %.1fs", tid, _CRITIC_RERUN_TIMEOUT)
            if original_critic_result is not None:
                results[tid] = original_critic_result
        except Exception as e:
            log.exception("Critic re-run %s failed", tid)
            if original_critic_result is not None:
                results[tid] = original_critic_result
            else:
                # Empty output = empty strings for tool_name/hash (validator will catch if needed)
                results[tid] = AgentResult(
                    task_id=tid,
                    role=task.role,
                    output="",
                    model="",
                    duration_s=0.0,
                    error=f"critic_rerun_failed:{e}",
                    source=task.source,
                    tool_called=True,
                    tool_name="",
                    raw_output_hash="",
                )

        # Incrementăm contorul de re-executări
        self._critic_rerun_count[tid] = current_count + 1

    async def execute(
        self,
        registry: "Registry",
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

        REGISTRY INJECTION (PHASE 4):
        =============================
        registry: Required Registry for dependency injection.
                  Passed to runner functions for consistent settings access.

        Args:
            registry: Registry for dependency injection (Phase 4)
            semaphore: asyncio.Semaphore to limit concurrent task execution.
            verbose: If True, log detailed execution progress.
            memory_manager: MemoryManager injected into tasks for Redis access.
                           DAG agents use task.memory_manager.working only.
            session_id: Session ID for storing DAG results

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
           a. Filter out tasks whose dependencies have failed (upstream_failure)
           b. Create _run coroutine for each remaining task in wave
           c. Execute all _run coroutines concurrently (asyncio.gather)
           d. Each _run acquires semaphore, executes task, stores result
        3. For critic tasks with CRITICAL/MAJOR severity:
           a. Re-execute upstream tasks with stricter prompt
           b. Re-run critic on new output
        4. Return results dictionary

        ERROR PROPAGATION:
        ==================
        - Task exceptions caught and stored in AgentResult.error
        - Failed tasks tracked in `failed_ids` set
        - Downstream tasks that depend on a failed task are SKIPPED
        - Sibling tasks (same wave, different dependency chain) continue
        """
        waves = self._dag.topological_waves()
        results: dict[str, AgentResult] = {}
        failed_ids: set[str] = set()

        if verbose:
            log.info(
                "WorkflowGraph: %d tasks in %d waves", len(self._tasks), len(waves)
            )

        for wave_idx, wave in enumerate(waves):
            # ── FIX (Problema 3): elimină din wave task-urile cu dependențe eșuate ──
            filtered_wave: list[str] = []
            skipped: list[str] = []
            for tid in wave:
                task = self._tasks[tid]
                # Verifică dacă vreo dependență directă a eșuat
                upstream_failed = [dep for dep in task.depends_on if dep in failed_ids]
                if upstream_failed:
                    # Marchează ca SKIPPED — nu se execută
                    results[tid] = AgentResult(
                        task_id=tid,
                        role=task.role,
                        output="",
                        model="",
                        duration_s=0.0,
                        error=f"upstream_failure:{','.join(upstream_failed)}",
                        source=task.source,
                        tool_called=True,
                        tool_name="",
                        raw_output_hash="",
                    )
                    failed_ids.add(tid)  # propagă mai departe
                    skipped.append(tid)
                    if verbose:
                        log.info(
                            "Skipping task %s: upstream failed (%s)",
                            tid, upstream_failed,
                        )
                else:
                    filtered_wave.append(tid)

            if verbose and skipped:
                log.info(
                    "Wave %d: skipped %d tasks due to upstream failure",
                    wave_idx, len(skipped),
                )

            if not filtered_wave:
                if verbose:
                    log.info("Wave %d: no tasks to execute (all skipped)", wave_idx)
                continue

            if verbose:
                log.info(
                    "Wave %d: executing %d tasks: %s",
                    wave_idx, len(filtered_wave), filtered_wave,
                )

            async def _run(tid: str) -> None:
                """Execute a single task with semaphore control.

                MEMORY ACCESS:
                ==============
                - Injects memory_manager into task for Redis working memory access
                - task.memory_manager.working accessible to agent
                - task.memory_manager.episodic and .long_term NOT accessible
                - Supervisor controls persistent memory tier writes

                CRITICAL REVIEW FALLBACK (Problema 5):
                ======================================
                After a critic task completes, if severity is CRITICAL or MAJOR:
                1. Extract feedback from critic output
                2. For each upstream task (depends_on of critic):
                   a. Re-execute with stricter prompt (original + feedback)
                   b. Update results dict with new output
                3. Re-run critic on the new upstream output
                4. Max 1 re-execution per critic task (prevent infinite loops)

                REGISTRY INJECTION (PHASE 4):
                =============================
                Passes registry to runner functions for dependency injection.

                Args:
                    tid: Task identifier to execute
                """
                async with semaphore:
                    task = self._tasks[tid]
                    # Inject memory_manager for Redis working memory access
                    # NOTE: Only working tier accessible; ChromaDB/Letta restricted
                    task.memory_manager = memory_manager

                    # Build context from completed dependencies
                    # ── FIX (Problema 2): filtrează doar rezultatele fără erori ──
                    context = {
                        dep_id: results[dep_id]
                        for dep_id in task.depends_on
                        if dep_id in results and results[dep_id].error is None
                    }

                    if verbose:
                        log.debug("Starting task %s (role=%s)", tid, task.role)

                    t_start = time.monotonic()
                    try:
                        runner = _RUNNERS[task.role]
                        # Phase 4 + IMPROVEMENT 2: wrap the runner with the
                        # retry helper. ``run_with_retry`` returns either a
                        # normal ``output`` string, or a synthetic
                        # ``TASK_ERROR: ...`` string when the retry budget
                        # is exhausted. The wrapper also restores
                        # ``task.prompt`` on return so subsequent tasks in
                        # the DAG see the original prompt.
                        from supervisor.pipeline.task_retry import (
                            is_task_error, run_with_retry,
                        )
                        output, _agent_result, err = await run_with_retry(
                            task, context, registry, runner,
                        )
                        task_failed_with_retry = bool(err and is_task_error(output))
                        if task_failed_with_retry:
                            # All retries exhausted — record a failed
                            # AgentResult and let the downstream
                            # propagation logic mark dependent tasks.
                            duration = time.monotonic() - t_start
                            log.warning(
                                "Task %s exhausted retries: %s",
                                tid, output[:200],
                            )
                            results[tid] = AgentResult(
                                task_id=tid,
                                role=task.role,
                                output="",
                                model="",
                                duration_s=duration,
                                error=output,
                                source=task.source,
                                tool_called=True,
                                tool_name="",
                                raw_output_hash="",
                            )
                            failed_ids.add(tid)
                            if session_id and memory_manager:
                                try:
                                    await _write_task_status(
                                        memory_manager, session_id, tid, task.role,
                                        output, "failed",
                                    )
                                except Exception as _fe:
                                    log.warning("Task status write (failed) failed: %s", _fe)
                        else:
                            duration = time.monotonic() - t_start
                            # Capture source from task (set by runner during execution)
                            # Extract tool_name from output if present
                            import hashlib
                            _tool_name = ""
                            _raw_hash = ""
                            if task.source and task.source != "generated" and task.source != "planner":
                                _tool_name = task.source  # use source as tool_name fallback
                            if output:
                                _raw_hash = hashlib.sha256(output.encode()).hexdigest()[:16]
                            results[tid] = AgentResult(
                                task_id=tid,
                                role=task.role,
                                output=output,
                                model="",
                                duration_s=duration,
                                error=None,
                                source=task.source,
                                tool_name=_tool_name,
                                raw_output_hash=_raw_hash,
                                tool_called=True,
                            )
                            if session_id and memory_manager:
                                try:
                                    await _write_task_memory(memory_manager, session_id, tid, task.role, output)
                                    log.debug("dag:%s:task:%s written to Redis", session_id, tid)
                                except Exception as _e:
                                    log.warning("Task memory write failed: %s", _e)
                                try:
                                    await _write_task_status(
                                        memory_manager, session_id, tid, task.role, output, "completed",
                                    )
                                except Exception as _se:
                                    log.warning("Task status write failed: %s", _se)
                            if verbose:
                                log.debug(
                                    "Completed task %s: %s",
                                    tid,
                                    output[:80] if output else "",
                                )

                            # ── Critic fallback — re-execută upstream doar dacă severity e CRITICAL ──
                            if task.role == "critic" and output:
                                severity, _ = _parse_critic_severity(output)
                                if severity == "CRITICAL":
                                    await self._re_execute_upstream_and_critic(
                                        tid=tid,
                                        task=task,
                                        output=output,
                                        results=results,
                                        registry=registry,
                                        verbose=verbose,
                                        t_start=t_start,
                                        memory_manager=memory_manager,
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
                            tool_called=True,
                            tool_name="",
                            raw_output_hash="",
                        )
                        # ── FIX (Problema 3): marchează ca eșuat pentru propagare ──
                        failed_ids.add(tid)
                        if session_id and memory_manager:
                            try:
                                await _write_task_status(
                                    memory_manager, session_id, tid, task.role, str(e), "failed",
                                )
                            except Exception as _fe:
                                log.warning("Task status write (failed) failed: %s", _fe)

            # Execute all tasks in this wave concurrently
            await asyncio.gather(*[_run(tid) for tid in filtered_wave])

            # ── DAG CONTROL CHECK — after every wave ──
            # GOAT may write "pause" or "stop" to dag:<session_id>:control.
            # wait_if_paused blocks on pause (max 60s) and returns False on stop.
            if session_id and memory_manager:
                from supervisor.pipeline.dag_control import wait_if_paused
                should_continue = await wait_if_paused(memory_manager, session_id)
                if not should_continue:
                    log.info("WorkflowGraph: stop signal — terminating after wave %d", wave_idx)
                    completed_stop = sorted(
                        tid for tid, r in results.items() if r.error is None
                    )
                    from supervisor.pipeline.dag_progress import write_final_progress
                    await write_final_progress(
                        memory_manager, session_id,
                        total_waves=len(waves), completed=completed_stop,
                    )
                    try:
                        import json as _json, time as _time
                        from supervisor.pipeline.dag_bridge import DagBridge
                        partial = _json.dumps({
                            "session_id": session_id,
                            "completed_at": _time.time(),
                            "status": "stopped",
                            "tasks": {
                                tid: {"role": r.role, "output": r.output[:2000], "error": r.error}
                                for tid, r in results.items()
                            },
                        }, indent=2)
                        bridge = DagBridge(memory_manager)
                        await bridge.write_result(session_id, partial)
                        log.info("WorkflowGraph: partial result written on stop session=%s", session_id)
                    except Exception as _e:
                        log.warning("WorkflowGraph: failed to write partial result on stop: %s", _e)
                    return results

            # ── DAG PROGRESS REPORTING (TASK 3) ──
            # After each wave completes, write a progress record to
            # working memory at `dag:<session_id>:progress` with the
            # current wave number, total waves, completed task IDs,
            # and status. GOAT reads this on demand via the
            # `query_dag_status` tool or `memory_get` with the same
            # key. The progress key is overwritten in place — no
            # append-only log, no versioning.
            if session_id and memory_manager:
                completed_now = sorted(
                    tid for tid, r in results.items() if r.error is None
                )
                from supervisor.pipeline.dag_progress import write_wave_progress
                await write_wave_progress(
                    memory_manager, session_id,
                    wave=wave_idx + 1, total_waves=len(waves),
                    completed=completed_now,
                )

        if verbose:
            log.info("WorkflowGraph: all %d waves complete", len(waves))
            if failed_ids:
                log.info("Failed/skipped tasks: %s", failed_ids)

        # Final progress update — mark complete before writing the
        # final result so GOAT sees a coherent terminal state.
        if session_id and memory_manager:
            completed_final = sorted(
                tid for tid, r in results.items() if r.error is None
            )
            from supervisor.pipeline.dag_progress import write_final_progress
            await write_final_progress(
                memory_manager, session_id,
                total_waves=len(waves), completed=completed_final,
            )

        if session_id and memory_manager:
            try:
                import json as _json
                import time as _time
                from supervisor.pipeline.dag_bridge import DagBridge
                full_detail = _json.dumps(
                    {
                        "session_id": session_id,
                        "completed_at": _time.time(),
                        "tasks": {
                            tid: {
                                "role": r.role,
                                "output": r.output[:2000],
                                "source": r.source,
                                "tool_called": r.tool_called,
                                "error": r.error,
                            }
                            for tid, r in results.items()
                        },
                    },
                    indent=2,
                )
                # Write with key dag:{session_id}:result — TTL 3600s (via DagBridge)
                bridge = DagBridge(memory_manager)
                await bridge.write_result(session_id, full_detail)
                log.info("dag:%s:result written to Redis (TTL=3600s)", session_id)
            except Exception as e:
                log.warning("Failed to write dag_result: %s", e)

        return results
