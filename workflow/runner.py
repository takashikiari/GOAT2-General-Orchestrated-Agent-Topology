"""
GOAT 2.0 — Workflow runner.

Pure DAG executor using Kahn's algorithm for topological sort.
Zero AI, zero LLM, zero tool calls — just graph execution.

The runner receives a ``DAGGraph``, validates it, topologically sorts
the nodes, then executes each node in order, propagating results
through a shared context dictionary.

Conditional execution
---------------------
Each node may declare a ``condition`` predicate (``Callable[[dict], bool]``).
If the predicate returns ``False``, the node is skipped — its runner is
not called, its result is ``None``, and it's recorded in ``WorkflowResult.skipped``.
Skipped nodes do **not** block subsequent nodes; downstream nodes decide
for themselves via their own conditions.

Per-node sandbox
----------------
If the graph has a ``working_dir`` set, each node gets a private folder
created at ``working_dir / dag_id / task_id /`` before it runs.
The path is injected into context as ``"__working_dir__"`` (a ``Path``).
Nodes never share a folder — zero race conditions on disk.

Cleanup
-------
Runner does **not** auto-clean. Call ``cleanup(dag_id)`` explicitly
when you want to remove a DAG's working directory. You decide when.

Usage::

    graph = DAGGraph(
        nodes=[...],
        dag_id="my-pipeline",
        working_dir=Path("/tmp/dags"),
    )
    runner = WorkflowRunner()
    result = await runner.run(graph, initial_context={"query": "..."})
    # inspect result, debug if needed, then:
    await runner.cleanup("my-pipeline")
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from shutil import rmtree
from typing import Any

from workflow.errors import (
    CycleDetected,
    NodeNotFound,
    WorkflowError,
)
from workflow.models import DAGGraph, WorkflowResult


class WorkflowRunner:
    """Pure DAG executor — topological sort + sequential node execution.

    Stateless by design: instantiate once, call ``run()`` as many times
    as needed. Each call builds its own execution context.
    """

    def __init__(self, working_dir: Path | None = None) -> None:
        """Store a reference working dir so cleanup knows where to look.

        Parameters
        ----------
        working_dir:
            Base directory under which DAG sandboxes live.
            If omitted, cleanup() will refuse to run — you must
            pass ``working_dir`` via the graph or the constructor.
        """
        self._working_dir = working_dir

    # ── public API ──────────────────────────────────────────────

    async def run(
        self,
        graph: DAGGraph,
        initial_context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a DAG from start to finish.

        Parameters
        ----------
        graph:
            The DAG to execute. Must contain at least one node and
            must be acyclic (validated at runtime).
        initial_context:
            Optional seed data passed to every node.

        Returns
        -------
        WorkflowResult
            Structured result with per-node outputs and execution
            order.
        """
        context: dict[str, Any] = dict(initial_context or {})
        results: dict[str, Any] = {}
        skipped: set[str] = set()
        errors: dict[str, Exception] = {}
        execution_order: list[str] = []

        order = self._topological_sort(graph)

        for node_id in order:
            node = graph.get_node(node_id)

            # ── per-node sandbox folder ─────────────────────────
            wd = graph.working_dir or self._working_dir
            if wd is not None:
                sandbox = wd / graph.dag_id / node_id
                sandbox.mkdir(parents=True, exist_ok=True)
                context["__working_dir__"] = sandbox

            # ── condition check ─────────────────────────────────
            if node.condition is not None:
                try:
                    should_run = node.condition(context)
                except Exception as exc:
                    errors[node_id] = exc
                    return WorkflowResult(
                        success=False,
                        results=results,
                        skipped=skipped,
                        errors=errors,
                        execution_order=tuple(execution_order),
                    )
                if not should_run:
                    skipped.add(node_id)
                    context[node_id] = None
                    execution_order.append(node_id)
                    continue

            # ── execute ─────────────────────────────────────────
            try:
                output = await node.runner(node_id, context)
                results[node_id] = output
                context[node_id] = output
                execution_order.append(node_id)
            except Exception as exc:
                errors[node_id] = exc
                return WorkflowResult(
                    success=False,
                    results=results,
                    skipped=skipped,
                    errors=errors,
                    execution_order=tuple(execution_order),
                )

        return WorkflowResult(
            success=True,
            results=results,
            skipped=skipped,
            errors=errors,
            execution_order=tuple(execution_order),
        )

    async def cleanup(self, dag_id: str) -> None:
        """Remove the working directory for a specific DAG.

        Only removes ``<working_dir>/<dag_id>/``.  Safe to call
        even if the folder doesn't exist — silently no-ops.

        Raises
        ------
        WorkflowError
            If no ``working_dir`` was configured (neither via
            constructor nor graph).
        """
        wd = self._working_dir
        if wd is None:
            raise WorkflowError(
                "Cannot cleanup: no working_dir configured. "
                "Pass it to the constructor or via DAGGraph."
            )
        target = wd / dag_id
        if target.is_dir():
            rmtree(target)

    # ── topological sort ────────────────────────────────────────

    def _topological_sort(self, graph: DAGGraph) -> list[str]:
        """Kahn's algorithm — returns nodes in execution order.

        Raises
        ------
        CycleDetected
            If the graph contains a cycle.
        NodeNotFound
            If a dependency references a non-existent node.
        """
        nodes_by_id = graph.nodes_by_id

        in_degree: dict[str, int] = {}
        adjacency: dict[str, list[str]] = {}

        for node in graph.nodes:
            nid = node.task_id
            in_degree.setdefault(nid, 0)
            adjacency.setdefault(nid, [])

        for node in graph.nodes:
            for dep_id in node.dependencies:
                if dep_id not in nodes_by_id:
                    raise NodeNotFound(
                        f"Dependency '{dep_id}' not found "
                        f"(required by '{node.task_id}')",
                        node_id=dep_id,
                        graph_nodes=list(nodes_by_id),
                    )
                adjacency.setdefault(dep_id, []).append(node.task_id)
                in_degree[node.task_id] = in_degree.get(node.task_id, 0) + 1

        queue: deque[str] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        order: list[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for successor in adjacency.get(nid, []):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        if len(order) != len(graph.nodes):
            remaining = set(nodes_by_id) - set(order)
            raise CycleDetected(
                f"Cycle detected among nodes: {sorted(remaining)}",
                remaining_nodes=sorted(remaining),
            )

        return order
