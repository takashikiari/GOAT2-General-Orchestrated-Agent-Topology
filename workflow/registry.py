"""
GOAT 2.0 — Workflow registry.

Stores named DAG graphs for the workflow engine.
Completely separate from AgentRegistry (agents/) and ServiceRegistry (registry/).
"""

from __future__ import annotations

from typing import Any

from workflow.errors import NodeNotFound
from workflow.models import DAGGraph
from workflow.runner import WorkflowRunner


class WorkflowRegistry:
    """Registry for named DAG workflows.

    Graphs are stored by name and retrieved on demand.
    A shared ``WorkflowRunner`` is lazily created on first use.
    """

    def __init__(self) -> None:
        self._graphs: dict[str, DAGGraph] = {}
        self._runner: WorkflowRunner | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, name: str, graph: DAGGraph) -> None:
        """Register a named DAG graph.

        Args:
            name: Unique workflow name.
            graph: The DAG to register.

        Raises:
            ValueError: If ``name`` is already registered.
        """
        if not isinstance(graph, DAGGraph):
            raise TypeError(f"Expected DAGGraph, got {type(graph).__name__}")

        if name in self._graphs:
            raise ValueError(f"Workflow {name!r} is already registered")

        self._graphs[name] = graph

    def get(self, name: str) -> DAGGraph:
        """Retrieve a registered DAG graph by name.

        Args:
            name: Workflow name.

        Returns:
            The registered ``DAGGraph``.

        Raises:
            NodeNotFound: If no workflow with that name exists.
        """
        graph = self._graphs.get(name)
        if graph is None:
            raise NodeNotFound(
                node_id=name,
                graph_nodes=list(self._graphs),
                message=f"Workflow {name!r} not found in registry",
            )
        return graph

    def unregister(self, name: str) -> None:
        """Remove a registered workflow.

        Args:
            name: Workflow name to remove.

        Raises:
            NodeNotFound: If no workflow with that name exists.
        """
        if name not in self._graphs:
            raise NodeNotFound(
                node_id=name,
                graph_nodes=list(self._graphs),
                message=f"Cannot unregister {name!r} — not found",
            )
        del self._graphs[name]

    def list(self) -> list[str]:
        """List all registered workflow names.

        Returns:
            Sorted list of workflow names.
        """
        return sorted(self._graphs)

    def clear(self) -> None:
        """Remove all registered workflows."""
        self._graphs.clear()

    # ------------------------------------------------------------------
    # Runner access
    # ------------------------------------------------------------------

    @property
    def runner(self) -> WorkflowRunner:
        """Lazy-initialized shared ``WorkflowRunner``."""
        if self._runner is None:
            self._runner = WorkflowRunner()
        return self._runner

    def run(
        self,
        name: str,
        initial_context: dict[str, Any] | None = None,
    ) -> Any:
        """Convenience: get a workflow by name and run it immediately.

        Args:
            name: Workflow name.
            initial_context: Optional initial context dict.

        Returns:
            ``WorkflowResult`` from the runner.
        """
        graph = self.get(name)
        return self.runner.run(graph, initial_context=initial_context)

    # ------------------------------------------------------------------
    # Container protocol
    # ------------------------------------------------------------------

    def __contains__(self, name: str) -> bool:
        return name in self._graphs

    def __len__(self) -> int:
        return len(self._graphs)

    def __repr__(self) -> str:
        return f"WorkflowRegistry({len(self)} workflows)"
