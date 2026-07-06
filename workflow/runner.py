"""workflow.runner — parallel async DAG executor.

Executes a ``DAGGraph`` with true asyncio concurrency: sibling nodes (no
mutual dependency) run as concurrent ``asyncio.Task`` s.  Execution order
follows topological constraints; nodes within the same dependency wave
overlap freely up to ``max_concurrent`` via a ``Semaphore``.

Cycle detection is performed upfront via Kahn's algorithm.  On the first
node failure (after all retries are exhausted) all in-flight tasks are
cancelled and a ``WorkflowResult`` with ``success=False`` is returned.

Per-node features
-----------------
timeout:
    ``TaskNode.timeout`` overrides the runner-level ``node_timeout`` for
    that specific node.  Useful when one agent is known to be slower.
max_retries / retry_delay:
    On transient failure or timeout the runner sleeps ``retry_delay``
    seconds (semaphore released so siblings can run) then re-attempts the
    node runner up to ``max_retries`` additional times.  The node is only
    marked as an error — and the DAG aborted — after all attempts fail.
confidence_score:
    ``WorkflowResult.confidence_score`` ∈ [0, 1].  1.0 = all nodes
    succeeded on first try.  Reduced proportionally by errors and by 3%
    per node that required at least one retry.

Streaming
---------
``on_node_change`` (optional, passed to ``run()``) is an async callback
with signature ``(node_id, new_state, all_node_states) -> None``.
It fires on every transition: running / retrying / done / error / skipped.
``DagChannel.publish_event`` fires alongside it so external subscribers
(Redis pub/sub) receive the same events.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from shutil import rmtree
from typing import Any

from workflow.errors import CycleDetected, NodeNotFound, WorkflowError
from workflow.models import DAGGraph, TaskNode, WorkflowResult

log = logging.getLogger("goat2.workflow.runner")

NodeChangeCallback = Callable[[str, str, dict[str, str]], Awaitable[None]]
"""Signature: ``async (node_id, new_state, all_node_states) -> None``"""


def _compute_confidence(
    execution_order: tuple[str, ...],
    errors: dict[str, Any],
    retries_used: dict[str, int],
) -> float:
    """Compute execution confidence score in [0.0, 1.0].

    base  = (nodes - errors) / nodes   (skipped nodes are neutral)
    penalty = 3% per node that needed at least one retry
    """
    total = len(execution_order)
    if not total:
        return 1.0
    base = (total - len(errors)) / total
    n_retried = sum(1 for v in retries_used.values() if v > 0)
    retry_penalty = n_retried * 0.03
    return round(max(0.0, min(1.0, base - retry_penalty)), 4)


class WorkflowRunner:
    """Parallel async DAG executor.

    Stateless by design: instantiate once, call ``run()`` many times.
    Each ``run()`` call owns its own execution context and task set.

    Args:
        working_dir: Base directory for per-node sandbox folders.
        max_concurrent: Max nodes running simultaneously within one DAG.
        node_timeout: Global per-node timeout in seconds.  Individual nodes
            may override this via ``TaskNode.timeout``.
    """

    def __init__(
        self,
        working_dir: Path | None = None,
        max_concurrent: int = 8,
        node_timeout: float = 300.0,
    ) -> None:
        self._working_dir = working_dir
        self._max_concurrent = max_concurrent
        self._node_timeout = node_timeout

    async def run(
        self,
        graph: DAGGraph,
        initial_context: dict[str, Any] | None = None,
        *,
        on_node_change: NodeChangeCallback | None = None,
    ) -> WorkflowResult:
        """Execute ``graph`` with parallel node scheduling.

        Args:
            graph: The validated DAG to run.
            initial_context: Optional seed data available to all nodes.
            on_node_change: Optional async callback fired on every node
                state transition.  Signature:
                ``async (node_id, state, all_node_states) -> None``
                States: ``running``, ``retrying``, ``done``, ``error``, ``skipped``.
                Exceptions in the callback are swallowed so they never abort
                the DAG.

        Returns:
            ``WorkflowResult`` with per-node outputs, skips, errors, and
            ``confidence_score``.

        Raises:
            CycleDetected: If the graph contains a cycle.
        """
        in_degree, adjacency = self._build_adjacency(graph)
        self._assert_acyclic(graph, in_degree, adjacency)

        context: dict[str, Any] = dict(initial_context or {})
        results: dict[str, Any] = {}
        skipped: set[str] = set()
        errors: dict[str, Exception] = {}
        order: list[str] = []
        node_states: dict[str, str] = {}
        retries_used: dict[str, int] = {}
        channel = context.get("__dag_channel__")
        sem = asyncio.Semaphore(self._max_concurrent)

        ready: asyncio.Queue[str] = asyncio.Queue()
        for nid, deg in in_degree.items():
            if deg == 0:
                ready.put_nowait(nid)

        active: dict[str, asyncio.Task] = {}
        remaining = len(graph.nodes)

        while remaining > 0:
            while not ready.empty():
                nid = ready.get_nowait()
                task = asyncio.create_task(
                    self._execute_node(
                        nid, graph, context, results, skipped, errors,
                        sem, node_states, retries_used, channel, on_node_change,
                    ),
                    name=nid,
                )
                active[nid] = task

            if not active:
                break  # guarded by cycle check above; shouldn't reach here

            done, _ = await asyncio.wait(active.values(), return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                nid = task.get_name()
                del active[nid]
                remaining -= 1
                order.append(nid)

                if nid in errors:
                    for pending in active.values():
                        pending.cancel()
                    await asyncio.gather(*active.values(), return_exceptions=True)
                    return WorkflowResult(
                        success=False,
                        results=results,
                        skipped=skipped,
                        errors=errors,
                        execution_order=tuple(order),
                        confidence_score=_compute_confidence(tuple(order), errors, retries_used),
                        retries_used=retries_used,
                    )

                context[nid] = results.get(nid)
                for successor in adjacency.get(nid, []):
                    in_degree[successor] -= 1
                    if in_degree[successor] == 0:
                        ready.put_nowait(successor)

        return WorkflowResult(
            success=True,
            results=results,
            skipped=skipped,
            errors=errors,
            execution_order=tuple(order),
            confidence_score=_compute_confidence(tuple(order), errors, retries_used),
            retries_used=retries_used,
        )

    async def cleanup(self, dag_id: str) -> None:
        """Remove the working directory for a DAG.  Silently no-ops if absent.

        Raises:
            WorkflowError: If no ``working_dir`` is configured.
        """
        wd = self._working_dir
        if wd is None:
            raise WorkflowError("Cannot cleanup: no working_dir configured.")
        target = wd / dag_id
        if target.is_dir():
            rmtree(target)

    # ── internal ─────────────────────────────────────────────────────────────

    async def _execute_node(
        self,
        nid: str,
        graph: DAGGraph,
        context: dict[str, Any],
        results: dict[str, Any],
        skipped: set[str],
        errors: dict[str, Exception],
        sem: asyncio.Semaphore,
        node_states: dict[str, str],
        retries_used: dict[str, int],
        channel: Any,
        on_node_change: NodeChangeCallback | None,
    ) -> None:
        node: TaskNode = graph.get_node(nid)
        # Fresh snapshot per attempt so retries see unmodified dependency outputs.
        base_ctx = dict(context)

        wd = graph.working_dir or self._working_dir
        if wd is not None:
            sandbox = wd / graph.dag_id / nid
            sandbox.mkdir(parents=True, exist_ok=True)
            base_ctx["__working_dir__"] = sandbox

        # ── condition check ──────────────────────────────────────────────────
        if node.condition is not None:
            try:
                should_run = node.condition(base_ctx)
            except Exception as exc:
                errors[nid] = exc
                await self._notify(nid, "error", node_states, channel, on_node_change)
                return
            if not should_run:
                skipped.add(nid)
                await self._notify(nid, "skipped", node_states, channel, on_node_change)
                return

        if node.runner is None:
            results[nid] = None
            return

        effective_timeout = node.timeout if node.timeout is not None else self._node_timeout

        # ── notify: running ──────────────────────────────────────────────────
        await self._notify(nid, "running", node_states, channel, on_node_change)
        log.info("node start  dag=%s node=%s", graph.dag_id, nid)

        # ── retry loop ───────────────────────────────────────────────────────
        attempt = 0
        while True:
            local_ctx = dict(base_ctx)  # fresh copy every attempt
            try:
                async with sem:
                    output = await asyncio.wait_for(
                        node.runner(nid, local_ctx),
                        timeout=effective_timeout,
                    )
                preview = (str(output or "")[:120] + "…") if len(str(output or "")) > 120 else str(output or "")
                log.info("node done   dag=%s node=%s output=%r", graph.dag_id, nid, preview)
                results[nid] = output
                retries_used[nid] = attempt
                await self._notify(nid, "done", node_states, channel, on_node_change)
                return

            except asyncio.TimeoutError as exc:
                if attempt >= node.max_retries:
                    log.error(
                        "node timeout dag=%s node=%s timeout=%.0fs attempts=%d",
                        graph.dag_id, nid, effective_timeout, attempt + 1,
                    )
                    errors[nid] = exc
                    retries_used[nid] = attempt
                    await self._notify(nid, "error", node_states, channel, on_node_change)
                    return
                attempt += 1
                log.warning(
                    "node timeout dag=%s node=%s timeout=%.0fs retrying=%d/%d",
                    graph.dag_id, nid, effective_timeout, attempt, node.max_retries,
                )
                await self._notify(nid, "retrying", node_states, channel, on_node_change)

            except Exception as exc:
                if attempt >= node.max_retries:
                    log.error(
                        "node error  dag=%s node=%s error=%s attempts=%d",
                        graph.dag_id, nid, exc, attempt + 1,
                    )
                    errors[nid] = exc
                    retries_used[nid] = attempt
                    await self._notify(nid, "error", node_states, channel, on_node_change)
                    return
                attempt += 1
                log.warning(
                    "node error  dag=%s node=%s error=%s retrying=%d/%d",
                    graph.dag_id, nid, exc, attempt, node.max_retries,
                )
                await self._notify(nid, "retrying", node_states, channel, on_node_change)

            # Sleep outside the semaphore so siblings can run during backoff.
            if node.retry_delay > 0:
                await asyncio.sleep(node.retry_delay)

    @staticmethod
    async def _notify(
        nid: str,
        state: str,
        node_states: dict[str, str],
        channel: Any,
        on_node_change: NodeChangeCallback | None,
    ) -> None:
        """Update node_states, write to Redis, fire pub/sub event and callback."""
        node_states[nid] = state
        snapshot = dict(node_states)

        if channel is not None:
            try:
                await channel.set_status("running", node_states=snapshot)
                await channel.publish_event(state, nid, snapshot)
            except Exception:
                pass

        if on_node_change is not None:
            try:
                await on_node_change(nid, state, snapshot)
            except Exception:
                pass

    @staticmethod
    def _build_adjacency(graph: DAGGraph) -> tuple[dict[str, int], dict[str, list[str]]]:
        in_degree: dict[str, int] = {node.task_id: 0 for node in graph.nodes}
        adjacency: dict[str, list[str]] = {node.task_id: [] for node in graph.nodes}
        for node in graph.nodes:
            for dep in node.dependencies:
                if dep not in in_degree:
                    raise NodeNotFound(dep, graph_nodes=list(in_degree))
                adjacency[dep].append(node.task_id)
                in_degree[node.task_id] += 1
        return in_degree, adjacency

    @staticmethod
    def _assert_acyclic(
        graph: DAGGraph,
        in_degree: dict[str, int],
        adjacency: dict[str, list[str]],
    ) -> None:
        degree = dict(in_degree)
        queue: deque[str] = deque(nid for nid, d in degree.items() if d == 0)
        visited = 0
        while queue:
            nid = queue.popleft()
            visited += 1
            for succ in adjacency.get(nid, []):
                degree[succ] -= 1
                if degree[succ] == 0:
                    queue.append(succ)
        if visited != len(graph.nodes):
            remaining = sorted(set(graph.nodes_by_id) - {n for n, d in degree.items() if d == 0})
            raise CycleDetected(
                f"Cycle detected among nodes: {remaining}",
                remaining_nodes=remaining,
            )
