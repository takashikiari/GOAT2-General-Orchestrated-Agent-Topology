"""supervisor — GOAT 2.0 workflow orchestration package.

PHASE 4 UPDATE: run() now requires ServiceRegistry parameter.
Legacy singleton fallback removed.

Directory structure:
    supervisor/behavior/   — Behavioral learning: style analysis, mirroring, persistence
    supervisor/pipeline/   — DAG execution: workflow, runners, validation
    supervisor/session/   — Session management: turns, history, memory injection
    supervisor/classification/ — Intent classification: depth routing, language detection
    supervisor/logging/    — Structured logging: audit, provenance, tool call tracing
    supervisor/interfaces/   — External interfaces (telegram_bot, content_filter)
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.supervisor")

from supervisor.types import (
    AgentRunner,
    TaskStatus,
    AgentTask,
    AgentResult,
    Plan,
    SupervisorResult,
)
from supervisor.registry import AgentRegistry
from supervisor.pipeline.workflow import WorkflowGraph
from supervisor.supervisor import GoatSupervisor

# Re-export from subdirectories for backward compatibility
from supervisor.behavior import (
    analyze_style,
    mirror_instruction,
    BehaviorProfile,
    serialize,
    deserialize,
    empty_profile,
    finalize_behavior,
    load_style,
    save_style,
    maybe_store_info,
    ScoredFact,
    INFERRED_TTL,
)

from supervisor.pipeline import (
    DAGraph,
    DAGNode,
    DAGEdge,
    validate_plan,
    validate_results,
    prepare_tasks,
    _run_researcher,
    _run_coder,
    _run_critic,
    _run_summarizer,
    _run_tool_caller,
)

from supervisor.session import (
    store_turn,
    store_dag_result,
    retrieve_dag_result,
    ConversationHistory,
    load_session_summary,
    init_session,
    mem_turn,
    recall_context,
)

from supervisor.classification import (
    IntentDepth,
    classify_intent,
    detect_language,
)

from supervisor.logging import (
    AuditReport,
    run_auditor,
    log_tool_call,
    SourceTag,
    TaggedResult,
    TOOL_SOURCE_MAP,
    infer_source,
)

# Re-export from moved locations for backward compatibility
# These modules were moved to more appropriate locations but are re-exported here
from agents.critique import critique_results, synthesize_results, CriticVerdict, parse_verdict
from agents.planner_decompose import decompose_plan
from tools.tool_runner import _call_with_tools
from utils.llm_utils import (
    _get_client,
    _call_llm,
    _extract_json,
    _extract_balanced_json,
    _format_dep_context,
    _model_label,
    _truncate_content,
    _truncate_messages,
)


async def run(
    intent: str,
    registry,
) -> SupervisorResult:
    """Top-level convenience entry point: asyncio.run(run('…')).

    PHASE 4: ServiceRegistry parameter is now REQUIRED.
    Legacy singleton fallback removed.

    Args:
        intent: User intent string
        registry: ServiceRegistry instance for dependency injection

    Example:
        from config.registry import ServiceRegistry
        from supervisor import run

        registry = ServiceRegistry()
        result = await run("Build a REST API", registry=registry)
    """
    return await GoatSupervisor(registry).run(intent)

__all__ = [
    # Core classes
    "GoatSupervisor",
    "AgentRegistry",
    "WorkflowGraph",
    "AgentRunner",
    "TaskStatus",
    "AgentTask",
    "AgentResult",
    "Plan",
    "SupervisorResult",
    "run",
    # Behavior
    "analyze_style",
    "mirror_instruction",
    "BehaviorProfile",
    "serialize",
    "deserialize",
    "empty_profile",
    "finalize_behavior",
    "load_style",
    "save_style",
    "maybe_store_info",
    "ScoredFact",
    "INFERRED_TTL",
    # Pipeline
    "DAGraph",
    "DAGNode",
    "DAGEdge",
    "validate_plan",
    "validate_results",
    "prepare_tasks",
    "_run_researcher",
    "_run_coder",
    "_run_critic",
    "_run_summarizer",
    "_run_tool_caller",
    # Session
    "store_turn",
    "store_dag_result",
    "retrieve_dag_result",
    "ConversationHistory",
    "load_session_summary",
    "init_session",
    "mem_turn",
    "recall_context",
    # Classification
    "IntentDepth",
    "classify_intent",
    "detect_language",
    # Logging
    "AuditReport",
    "run_auditor",
    "log_tool_call",
    "SourceTag",
    "TaggedResult",
    "TOOL_SOURCE_MAP",
    "infer_source",
    # Re-exported from moved modules
    "critique_results",
    "synthesize_results",
    "CriticVerdict",
    "parse_verdict",
    "decompose_plan",
    "_call_with_tools",
    "_get_client",
    "_call_llm",
    "_extract_json",
    "_extract_balanced_json",
    "_format_dep_context",
    "_model_label",
    "_truncate_content",
    "_truncate_messages",
]