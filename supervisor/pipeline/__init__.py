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
from tools.dag.validators import (
    ValidationReport,
    ValidationStatus,
    validate_dag_result,
    validate_results,
)
from supervisor.pipeline.dag_bridge import DagBridge
from memory.memory_promoter import MemoryPromoter
from supervisor.pipeline.task_prep import prepare_tasks
from supervisor.pipeline.runners import (
    _run_researcher,
    _run_coder,
    _run_critic,
    _run_summarizer,
    _run_tool_caller,
)
from supervisor.pipeline.critic_rerun import STRICTER_SYSTEM_PROMPTS, _rerun_failed_tasks

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
    "ValidationStatus",
    "validate_dag_result",
    "MemoryPromoter",
    "prepare_tasks",
    "_run_researcher",
    "_run_coder",
    "_run_critic",
    "_run_summarizer",
    "_run_tool_caller",
    "STRICTER_SYSTEM_PROMPTS",
    "_rerun_failed_tasks",
]