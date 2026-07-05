"""workflow.runner — parallel async DAG executor.

Executes a ``DAGGraph`` with true asyncio concurrency: sibling nodes (no
mutual dependency) run as concurrent ``asyncio.Task`` s.  Execution order
follows topological constraints; nodes within the same dependency wave
overlap freely up to ``max_concurrent`` via a ``Semaphore``.

Cycle detection is performed upfront via Kahn's algorithm.  On the first
node failure all in-flight tasks are cancelled and a ``WorkflowResult``
with ``success=False`` is returned immediately.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path
from shutil import rmtree
from typing import Any

from workflow.errors import CycleDetected, NodeNotFound, WorkflowError
from workflow.models import DAGGraph, TaskNode, WorkflowResult

log = logging.getLogger("goat2.workflow.runner")


class WorkflowRunner:
    """Parallel async DAG executor.

    Stateless by design: instantiate once, call ``run()`` many times.
    Each ``run()`` call owns its own execution context and task set.

    Args:
        working_dir: Base directory for per-node sandbox folders.
        max_concurrent: Max nodes running simultaneously within one DAG.
        node_timeout: Per-node timeout in seconds (``asyncio.TimeoutError``
            is caught and recorded in ``WorkflowResult.errors``).
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
    ) -> WorkflowResult:
        """Execute ``graph`` with parallel node scheduling.

        Args:
            graph: The validated DAG to run.
            initial_context: Optional seed data available to all nodes.

        Returns:
            ``WorkflowResult`` with per-node outputs, skips, and errors.

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
                    self._execute_node(nid, graph, context, results, skipped, errors, sem, node_states, channel),
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
        channel: Any,
    ) -> None:
        node: TaskNode = graph.get_node(nid)
        local_ctx = dict(context)  # snapshot: deps already present at launch time

        wd = graph.working_dir or self._working_dir
        if wd is not None:
            sandbox = wd / graph.dag_id / nid
            sandbox.mkdir(parents=True, exist_ok=True)
            local_ctx["__working_dir__"] = sandbox

        if node.condition is not None:
            try:
                should_run = node.condition(local_ctx)
            except Exception as exc:
                errors[nid] = exc
                return
            if not should_run:
                skipped.add(nid)
                node_states[nid] = "skipped"
                return

        if node.runner is None:
            results[nid] = None
            return

        node_states[nid] = "running"
        if channel is not None:
            try:
                await channel.set_status("running", node_states=dict(node_states))
            except Exception:
                pass

        log.info("node start  dag=%s node=%s", graph.dag_id, nid)
        try:
            async with sem:
                output = await asyncio.wait_for(
                    node.runner(nid, local_ctx),
                    timeout=self._node_timeout,
                )
            preview = (str(output or "")[:120] + "…") if len(str(output or "")) > 120 else str(output or "")
            log.info("node done   dag=%s node=%s output=%r", graph.dag_id, nid, preview)
            results[nid] = output
            node_states[nid] = "done"
        except asyncio.TimeoutError as exc:
            log.error("node timeout dag=%s node=%s timeout=%.0fs", graph.dag_id, nid, self._node_timeout)
            errors[nid] = exc
            node_states[nid] = "error"
        except Exception as exc:
            log.error("node error  dag=%s node=%s error=%s", graph.dag_id, nid, exc)
            errors[nid] = exc
            node_states[nid] = "error"

        if channel is not None:
            try:
                await channel.set_status("running", node_states=dict(node_states))
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
