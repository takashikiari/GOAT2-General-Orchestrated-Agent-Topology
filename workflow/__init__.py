"""
GOAT 2.0 — Workflow package.

Pure DAG workflow engine with topological sort (Kahn's algorithm),
typed node/graph models, and a named-workflow registry.

Public API:
    WorkflowRunner      — execute a DAGGraph
    WorkflowRegistry    — store & retrieve named DAGs
    TaskNode            — a single node in the DAG
    DAGGraph            — a directed acyclic graph of TaskNodes
    WorkflowResult      — outcome of a workflow run
"""

from workflow.models import TaskNode, DAGGraph, WorkflowResult
from workflow.runner import WorkflowRunner
from workflow.registry import WorkflowRegistry

__all__ = [
    "TaskNode",
    "DAGGraph",
    "WorkflowResult",
    "WorkflowRunner",
    "WorkflowRegistry",
]
