"""
GOAT 2.0 — Workflow errors.

Domain-specific exceptions for the DAG workflow engine.
Every error carries structured context for debugging and
recovery — never a bare string.
"""

from __future__ import annotations

from typing import Any


class WorkflowError(Exception):
    """Base exception for all workflow-domain errors."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        self.context = context or {}
        super().__init__(message)


class CycleDetected(WorkflowError):
    """A cycle was found in the DAG — execution cannot proceed.

    The topological sort (Kahn's algorithm) detected that
    the remaining graph still has edges after removing all
    zero-in-degree nodes.
    """

    def __init__(
        self,
        message: str,
        *,
        remaining_nodes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.remaining_nodes = remaining_nodes or []
        ctx = {"remaining_nodes": self.remaining_nodes, **(context or {})}
        super().__init__(message, context=ctx)


class NodeNotFound(WorkflowError):
    """A referenced node does not exist in the DAG.

    Raised when a dependency edge points to a ``task_id``
    that was never registered in the ``DAGGraph``.
    """

    def __init__(
        self,
        node_id: str,
        *,
        graph_nodes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.node_id = node_id
        self.graph_nodes = graph_nodes or []
        msg = f"Node '{node_id}' not found in graph"
        ctx = {"node_id": node_id, "graph_nodes": self.graph_nodes, **(context or {})}
        super().__init__(msg, context=ctx)


class DependencyMissing(WorkflowError):
    """A node's dependency did not produce a result.

    Raised during execution when the context dict lacks an
    entry for a dependency that should have been executed
    earlier in the topological order.
    """

    def __init__(
        self,
        node_id: str,
        dependency_id: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.node_id = node_id
        self.dependency_id = dependency_id
        msg = f"Node '{node_id}' missing dependency '{dependency_id}'"
        ctx = {
            "node_id": node_id,
            "dependency_id": dependency_id,
            **(context or {}),
        }
        super().__init__(msg, context=ctx)


class WorkflowExecutionError(WorkflowError):
    """A node runner raised an exception during execution.

    Wraps the original exception so the caller can inspect
    both the node that failed and the underlying cause.
    """

    def __init__(
        self,
        node_id: str,
        original_exception: Exception,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.node_id = node_id
        self.original_exception = original_exception
        msg = f"Node '{node_id}' failed: {original_exception}"
        ctx = {
            "node_id": node_id,
            "original_type": type(original_exception).__name__,
            "original_message": str(original_exception),
            **(context or {}),
        }
        super().__init__(msg, context=ctx)
