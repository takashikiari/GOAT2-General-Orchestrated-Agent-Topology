"""workflow.registry — named DAG graph store.

Decoupled from ``WorkflowRunner`` and ``DagManager``:
the registry only stores graph definitions; execution is the manager's job.
"""
from __future__ import annotations

from typing import Any

from workflow.errors import WorkflowNotFound
from workflow.models import DAGGraph


class WorkflowRegistry:
    """In-memory registry for named ``DAGGraph`` definitions.

    Graphs are registered by name and retrieved on demand.
    Thread-safe for read access; writes are not concurrent-safe
    (register / unregister at startup, not during execution).

    No singleton — instantiate one per application scope.
    """

    def __init__(self) -> None:
        self._graphs: dict[str, DAGGraph] = {}

    # ── registration ─────────────────────────────────────────────────────────

    def register(self, name: str, graph: DAGGraph) -> None:
        """Register a named DAG graph.

        Args:
            name: Unique workflow name.
            graph: The ``DAGGraph`` to store.

        Raises:
            TypeError: If ``graph`` is not a ``DAGGraph``.
            ValueError: If ``name`` is already registered.
        """
        if not isinstance(graph, DAGGraph):
            raise TypeError(f"Expected DAGGraph, got {type(graph).__name__}")
        if name in self._graphs:
            raise ValueError(f"Workflow {name!r} is already registered")
        self._graphs[name] = graph

    def get(self, name: str) -> DAGGraph:
        """Retrieve a registered DAG graph by name.

        Raises:
            WorkflowNotFound: If no workflow with that name exists.
        """
        graph = self._graphs.get(name)
        if graph is None:
            raise WorkflowNotFound(name, registered=list(self._graphs))
        return graph

    def unregister(self, name: str) -> None:
        """Remove a registered workflow by name.

        Raises:
            WorkflowNotFound: If no workflow with that name exists.
        """
        if name not in self._graphs:
            raise WorkflowNotFound(name, registered=list(self._graphs))
        del self._graphs[name]

    def list(self) -> list[str]:
        """Return a sorted list of all registered workflow names."""
        return sorted(self._graphs)

    def clear(self) -> None:
        """Remove all registered workflows."""
        self._graphs.clear()

    # ── container protocol ────────────────────────────────────────────────────

    def __contains__(self, name: str) -> bool:
        return name in self._graphs

    def __len__(self) -> int:
        return len(self._graphs)

    def __repr__(self) -> str:
        return f"WorkflowRegistry({len(self)} workflows: {self.list()})"
