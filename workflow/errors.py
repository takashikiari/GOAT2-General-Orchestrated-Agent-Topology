"""workflow.errors — domain exceptions for the DAG workflow engine.

Every error carries structured context for debugging and recovery.
"""
from __future__ import annotations

from typing import Any


class WorkflowError(Exception):
    """Base exception for all workflow-domain errors."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        self.context = context or {}
        super().__init__(message)


class CycleDetected(WorkflowError):
    """A cycle was found in the DAG — execution cannot proceed."""

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
    """A referenced node does not exist in the DAG."""

    def __init__(
        self,
        node_id: str,
        *,
        message: str | None = None,
        graph_nodes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.node_id = node_id
        self.graph_nodes = graph_nodes or []
        msg = message or f"Node '{node_id}' not found in graph"
        ctx = {"node_id": node_id, "graph_nodes": self.graph_nodes, **(context or {})}
        super().__init__(msg, context=ctx)


class DependencyMissing(WorkflowError):
    """A node's dependency did not produce a result."""

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
        ctx = {"node_id": node_id, "dependency_id": dependency_id, **(context or {})}
        super().__init__(msg, context=ctx)


class WorkflowExecutionError(WorkflowError):
    """A node runner raised an exception during execution."""

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


class WorkflowNotFound(WorkflowError):
    """A named workflow was not found in the registry."""

    def __init__(
        self,
        name: str,
        *,
        registered: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.registered = registered or []
        msg = f"Workflow '{name}' not found in registry"
        ctx = {"name": name, "registered": self.registered, **(context or {})}
        super().__init__(msg, context=ctx)
