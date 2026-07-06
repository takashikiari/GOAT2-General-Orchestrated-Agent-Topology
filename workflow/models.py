"""
GOAT 2.0 — Workflow models.

Typed data structures for the DAG workflow engine:
``TaskNode``, ``DAGGraph``, and ``WorkflowResult``.

Every component in the workflow package depends on these types.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Type alias — the signature every DAG node runner must satisfy
# ---------------------------------------------------------------------------

NodeRunner = Callable[[str, dict[str, Any]], Awaitable[Any]]
"""Signature: ``async def runner(task_id: str, context: dict[str, Any]) -> Any``

- ``task_id`` — the id of the node being executed (so the runner knows what to do).
- ``context`` — shared dict populated with results from already-completed nodes.
  Also contains ``"__working_dir__"`` (``Path``) — the node's private sandbox folder.
- Returns the result for this node, which will be stored in ``context[task_id]``.
"""

# Type alias for a condition predicate
ConditionFn = Callable[[dict[str, Any]], bool]
"""Signature: ``def condition(context: dict[str, Any]) -> bool``

Receives the shared context (including results from completed nodes).
Return ``True`` to execute the node, ``False`` to skip it.
"""


# ---------------------------------------------------------------------------
# TaskNode — a single vertex in the DAG
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class TaskNode:
    """A single node (vertex) in the workflow DAG.

    Attributes
    ----------
    task_id:
        Unique identifier for this node. Used as key in the context dict.
    dependencies:
        List of ``task_id`` s that must complete before this node runs.
        May be empty (root node).
    runner:
        Async callable that executes the node's work.
    condition:
        Optional predicate. If set and returns ``False``, the node is
        skipped — its runner is not called and its result is ``None``.
        The predicate receives the shared context dict.
    metadata:
        Optional arbitrary payload (tags, priority, ...).
    timeout:
        Per-node execution timeout in seconds. Overrides the runner-level
        ``node_timeout`` when set. ``None`` means use the runner default.
    max_retries:
        Number of additional attempts after the first failure. 0 means no
        retries (fail immediately). Each attempt uses the same runner and
        a fresh copy of the context snapshot.
    retry_delay:
        Seconds to wait between retry attempts. The semaphore is released
        during the sleep so other nodes can run concurrently.
    """

    task_id: str
    dependencies: tuple[str, ...] = ()
    runner: NodeRunner | None = None
    condition: ConditionFn | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    timeout: float | None = None
    max_retries: int = 0
    retry_delay: float = 0.0

    def __post_init__(self) -> None:
        """Validate basic invariants."""
        if not self.task_id or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        if self.runner is not None and not callable(self.runner):
            raise TypeError(f"runner must be callable or None, got {type(self.runner)}")
        if self.condition is not None and not callable(self.condition):
            raise TypeError(
                f"condition must be callable or None, got {type(self.condition)}"
            )
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")
        if self.retry_delay < 0:
            raise ValueError(f"retry_delay must be >= 0, got {self.retry_delay}")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {self.timeout}")


# ---------------------------------------------------------------------------
# DAGGraph — the full graph definition
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class DAGGraph:
    """An executable DAG: a collection of ``TaskNode`` s with implicit edges.

    Edges are defined by each node's ``dependencies`` tuple.
    The graph is validated at construction time:

    - All dependency references point to existing nodes.
    - No duplicate ``task_id`` s.
    - At least one node is present.

    Parameters
    ----------
    nodes:
        The DAG vertices.
    dag_id:
        Optional unique identifier for this DAG. Used as working-dir
        subfolder name when ``working_dir`` is set.
    working_dir:
        Optional root path for per-node sandbox folders.
        If set, each node gets ``working_dir / dag_id / task_id /``
        created before execution, available in context as
        ``"__working_dir__"``.
    """

    nodes: tuple[TaskNode, ...]
    dag_id: str = "default"
    working_dir: Path | None = None

    def __post_init__(self) -> None:
        """Validate the graph on construction."""
        if not self.nodes:
            raise ValueError("DAGGraph must contain at least one node")

        ids: set[str] = set()
        for node in self.nodes:
            if node.task_id in ids:
                raise ValueError(f"Duplicate task_id: {node.task_id!r}")
            ids.add(node.task_id)

        for node in self.nodes:
            for dep in node.dependencies:
                if dep not in ids:
                    raise ValueError(
                        f"Node {node.task_id!r} depends on {dep!r}, "
                        f"but no such node exists in the graph"
                    )

    # -- convenience helpers ------------------------------------------------

    def get_node(self, task_id: str) -> TaskNode:
        """Return the node with *task_id*, or raise ``KeyError``."""
        for node in self.nodes:
            if node.task_id == task_id:
                return node
        raise KeyError(f"Node {task_id!r} not found in graph")

    def has_node(self, task_id: str) -> bool:
        """Return ``True`` if *task_id* exists in this graph."""
        return any(node.task_id == task_id for node in self.nodes)

    @property
    def nodes_by_id(self) -> dict[str, TaskNode]:
        """Quick lookup: ``{task_id: TaskNode}``."""
        return {node.task_id: node for node in self.nodes}

    @property
    def root_ids(self) -> tuple[str, ...]:
        """Return ids of nodes with zero dependencies (roots)."""
        return tuple(
            node.task_id for node in self.nodes if not node.dependencies
        )

    @property
    def leaf_ids(self) -> tuple[str, ...]:
        """Return ids of nodes that are not a dependency of any other node."""
        depended: set[str] = set()
        for node in self.nodes:
            depended.update(node.dependencies)
        return tuple(
            node.task_id
            for node in self.nodes
            if node.task_id not in depended
        )


# ---------------------------------------------------------------------------
# WorkflowResult — what the runner returns
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class WorkflowResult:
    """Outcome of a single ``WorkflowRunner.run()`` call.

    Attributes
    ----------
    success:
        ``True`` if every node completed without error.
    results:
        Mapping from ``task_id`` to the value returned by its runner.
        Only contains entries for nodes that actually ran (not skipped).
    skipped:
        Set of ``task_id`` s that were skipped because their condition
        returned ``False``.
    errors:
        Mapping from ``task_id`` to the exception that was raised.
        Empty on a fully successful run.
    execution_order:
        The topological order in which nodes were executed (including
        skipped nodes — they still appear in order).
    confidence_score:
        Float in [0.0, 1.0]. 1.0 means every node succeeded on first
        attempt. Reduced by node failures (proportional) and by retries
        (3% per node that needed at least one retry). Skipped nodes are
        neutral (intentional branching does not reduce confidence).
    retries_used:
        Mapping from ``task_id`` to the number of retries consumed (0 for
        nodes that succeeded or failed on the first attempt).
    """

    success: bool
    results: dict[str, Any] = dataclasses.field(default_factory=dict)
    skipped: set[str] = dataclasses.field(default_factory=set)
    errors: dict[str, Exception] = dataclasses.field(default_factory=dict)
    execution_order: tuple[str, ...] = ()
    confidence_score: float = 0.0
    retries_used: dict[str, int] = dataclasses.field(default_factory=dict)
