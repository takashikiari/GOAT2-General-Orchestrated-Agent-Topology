"""workflow — parallel async DAG engine with Redis-backed orchestrator comms.

Public API
----------
Core graph types:
    TaskNode, DAGGraph, WorkflowResult, NodeRunner, ConditionFn

Execution:
    WorkflowRunner     — parallel async DAG executor
    WorkflowRegistry   — named graph store

Background management:
    DagManager         — asyncio.Task lifecycle + channel wiring
    DagChannel         — Redis K/V channel per DAG run

Agent routing:
    AgentRouter        — role → agent class (lazy imports)

Configuration:
    WorkflowConfig     — immutable settings dataclass

Errors:
    WorkflowError, CycleDetected, NodeNotFound,
    DependencyMissing, WorkflowExecutionError, WorkflowNotFound
"""

from workflow.models import DAGGraph, NodeRunner, ConditionFn, TaskNode, WorkflowResult
from workflow.runner import WorkflowRunner
from workflow.registry import WorkflowRegistry
from workflow.dag_channel import DagChannel
from workflow.dag_manager import DagManager
from workflow.routing import AgentRouter
from workflow.config import WorkflowConfig
from workflow.errors import (
    WorkflowError,
    CycleDetected,
    NodeNotFound,
    DependencyMissing,
    WorkflowExecutionError,
    WorkflowNotFound,
)

__all__ = [
    # models
    "TaskNode",
    "DAGGraph",
    "WorkflowResult",
    "NodeRunner",
    "ConditionFn",
    # execution
    "WorkflowRunner",
    "WorkflowRegistry",
    # background
    "DagManager",
    "DagChannel",
    # routing
    "AgentRouter",
    # config
    "WorkflowConfig",
    # errors
    "WorkflowError",
    "CycleDetected",
    "NodeNotFound",
    "DependencyMissing",
    "WorkflowExecutionError",
    "WorkflowNotFound",
]
