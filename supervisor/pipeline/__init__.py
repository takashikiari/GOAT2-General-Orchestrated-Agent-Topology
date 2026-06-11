"""DAG execution pipeline for GOAT 2.0 — workflow, runners, and validation.

Exports:
    - WorkflowGraph: DAG-based task execution with wave concurrency
    - DAGraph, DAGNode, DAGEdge: Core DAG data structures
    - TaskStatus: Node execution state
    - validate_plan: Pre-execution plan validation
    - validate_results: Post-execution result validation
    - prepare_tasks: Inject memory_manager and language into tasks
    - _run_researcher, _run_coder, _run_critic, _run_summarizer, _run_tool_caller: Agent runners
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.supervisor.pipeline")

from supervisor.pipeline.workflow import WorkflowGraph
from supervisor.pipeline.dag import DAGraph, DAGNode, DAGEdge, TaskStatus
from supervisor.pipeline.plan_validator import validate_plan
from supervisor.pipeline.dag_validator import validate_results
from supervisor.pipeline.dag_bridge import DagBridge
from supervisor.pipeline.goat_validator import ValidationReport, validate_dag_result
from memory.memory_promoter import MemoryPromoter
from supervisor.pipeline.task_prep import prepare_tasks
from supervisor.pipeline.runners import (
    _run_researcher,
    _run_coder,
    _run_critic,
    _run_summarizer,
    _run_tool_caller,
)

__all__ = [
    "WorkflowGraph",
    "DAGraph",
    "DAGNode",
    "DAGEdge",
    "TaskStatus",
    "validate_plan",
    "validate_results",
    "DagBridge",
    "ValidationReport",
    "validate_dag_result",
    "MemoryPromoter",
    "prepare_tasks",
    "_run_researcher",
    "_run_coder",
    "_run_critic",
    "_run_summarizer",
    "_run_tool_caller",
]