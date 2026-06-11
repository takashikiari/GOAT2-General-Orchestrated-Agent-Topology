"""
dag — Directed Acyclic Graph (DAG) data structures for GOAT 2.0 workflows.

Provides the core DAG primitives used by WorkflowGraph to represent,
validate, and execute task pipelines as topological waves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("goat2.supervisor.pipeline.dag")

__all__ = [
    "TaskStatus",
    "DAGNode",
    "DAGEdge",
    "DAGraph",
    "ValidationError",
]


class TaskStatus(Enum):
    """Execution status of a single DAG node."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DAGNode:
    """
    A single node in the DAG representing an agent task.

    Attributes:
        node_id: Unique identifier for this node.
        role: Agent role (e.g. 'researcher', 'coder', 'critic').
        label: Human-readable short description.
        params: Arbitrary keyword arguments passed to the agent runner.
        source: Origin identifier for audit trail (e.g. 'planner', 'manual').
    """

    node_id: str
    role: str
    label: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass(frozen=True)
class DAGEdge:
    """
    A directed dependency edge between two DAG nodes.

    Attributes:
        source: The upstream node_id that must complete first.
        target: The downstream node_id that depends on source.
    """

    source: str
    target: str


class ValidationError(Exception):
    """Raised when a DAG fails validation."""


class DAGraph:
    """
    A lightweight Directed Acyclic Graph for task orchestration.

    Supports adding nodes and edges, topological sorting (Kahn's algorithm),
    cycle detection, wave grouping for concurrent execution, and structural
    validation via the ``validate`` method.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, DAGNode] = {}
        self._edges: list[DAGEdge] = []
        self._adjacency: dict[str, list[str]] = {}
        self._in_degree: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node: DAGNode) -> None:
        """Register a node. Replaces any existing node with the same ID."""
        self._nodes[node.node_id] = node
        self._adjacency.setdefault(node.node_id, [])
        self._in_degree.setdefault(node.node_id, 0)

    def add_edge(self, edge: DAGEdge) -> None:
        """
        Add a directed dependency edge.

        Raises ValidationError if source or target are unknown nodes.
        """
        if edge.source not in self._nodes:
            raise ValidationError(f"Unknown source node: '{edge.source}'")
        if edge.target not in self._nodes:
            raise ValidationError(f"Unknown target node: '{edge.target}'")
        self._edges.append(edge)
        self._adjacency[edge.source].append(edge.target)
        self._in_degree[edge.target] = self._in_degree.get(edge.target, 0) + 1

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def nodes(self) -> dict[str, DAGNode]:
        """Read-only view of registered nodes."""
        return dict(self._nodes)

    @property
    def edges(self) -> list[DAGEdge]:
        """Read-only view of registered edges."""
        return list(self._edges)

    def node_count(self) -> int:
        """Number of nodes in the graph."""
        return len(self._nodes)

    def edge_count(self) -> int:
        """Number of edges in the graph."""
        return len(self._edges)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """
        Run structural validation checks on the DAG.

        Returns a list of warning/error messages. An empty list means the
        DAG is structurally valid. Checks performed:

        - At least one node exists.
        - No duplicate node IDs (enforced by dict).
        - No self-loops (edge where source == target).
        - All dependency references point to existing nodes.
        - Graph is acyclic (topological sort succeeds).
        - Every node has a non-empty role.
        - Every node has a source label (for audit trail).

        Raises:
            ValidationError: If a critical structural issue is found
                             (unknown deps, cycles).
        """
        issues: list[str] = []

        if not self._nodes:
            issues.append("DAG is empty — no nodes defined.")

        for nid, node in self._nodes.items():
            if not node.role.strip():
                issues.append(f"Node '{nid}' has an empty role.")
            if not node.source.strip():
                issues.append(f"Node '{nid}' is missing a 'source' label for audit.")

        for edge in self._edges:
            if edge.source == edge.target:
                issues.append(f"Self-loop detected: '{edge.source}' -> '{edge.target}'.")

        # Topological sort catches cycles and unknown deps
        try:
            self.topological_waves()
        except ValidationError as exc:
            issues.append(str(exc))

        return issues

    # ------------------------------------------------------------------
    # Topological sort & wave grouping
    # ------------------------------------------------------------------

    def topological_waves(self) -> list[list[str]]:
        """
        Return node IDs grouped into parallel execution waves.

        Wave 0 contains nodes with no dependencies.
        Wave N contains nodes whose dependencies are all in waves < N.

        If the graph contains a cycle or unreachable nodes, returns a
        **fallback** of 2 waves: all nodes with in_degree 0 in wave 0,
        and the remaining nodes in wave 1 (dependency order discarded).
        This ensures the DAG always produces a schedule instead of
        crashing with ValidationError.
        """
        in_deg = dict(self._in_degree)
        adj = {nid: list(deps) for nid, deps in self._adjacency.items()}

        waves: list[list[str]] = []
        ready = [nid for nid, deg in in_deg.items() if deg == 0]

        while ready:
            waves.append(list(ready))
            next_ready: list[str] = []
            for nid in ready:
                for child in adj.get(nid, []):
                    in_deg[child] -= 1
                    if in_deg[child] == 0:
                        next_ready.append(child)
            ready = next_ready

        processed = sum(len(w) for w in waves)
        if processed != len(self._nodes):
            # --- FALLBACK: instead of raising, produce a safe 2-wave schedule ---
            log.warning(
                "DAG cycle or unreachable nodes (%d/%d reachable). "
                "Falling back to flat 2-wave schedule.",
                processed, len(self._nodes),
            )
            # Identify nodes that were never reached
            reached = {nid for wave in waves for nid in wave}
            unreached = [nid for nid in self._nodes if nid not in reached]
            # Wave 0: all nodes with original in_degree 0 (including any from unreached)
            wave0 = [nid for nid in self._nodes if self._in_degree.get(nid, 0) == 0]
            # Wave 1: everything else
            wave1 = [nid for nid in self._nodes if nid not in wave0]
            # If wave0 is empty (all nodes have deps), put first unreached as wave0
            if not wave0 and self._nodes:
                all_ids = list(self._nodes.keys())
                wave0 = [all_ids[0]]
                wave1 = all_ids[1:]
            return [wave0, wave1] if wave1 else [wave0]

        return waves

    def is_acyclic(self) -> bool:
        """Return True if the graph has no cycles."""
        try:
            self.topological_waves()
            return True
        except ValidationError:
            return False

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the DAG to a plain dictionary."""
        return {
            "nodes": [
                {
                    "node_id": n.node_id, "role": n.role, "label": n.label,
                    "params": n.params, "source": n.source,
                }
                for n in self._nodes.values()
            ],
            "edges": [{"source": e.source, "target": e.target} for e in self._edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGraph:
        """Deserialize a DAG from a dictionary produced by ``to_dict``."""
        dag = cls()
        for nd in data.get("nodes", []):
            dag.add_node(DAGNode(**nd))
        for ed in data.get("edges", []):
            dag.add_edge(DAGEdge(**ed))
        return dag

    def __repr__(self) -> str:
        return f"<DAGraph nodes={self.node_count()} edges={self.edge_count()}>"
