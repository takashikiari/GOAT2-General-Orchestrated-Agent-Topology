"""GOAT 2.0 top-level orchestrator — unified message handling with autonomous tool selection.

GOAT supervisor manages memory read/write directly across all three tiers (Redis, ChromaDB, Letta).
DAG agents access tools but are restricted to working memory (Redis) with role="user_session".
GOAT validates task success by checking tool parameters — never reports validated without verification.

MEMORY ACCESS ARCHITECTURE:
===========================
This module implements a tiered memory access model for security and data integrity:

SUPERVISOR (Full Access):
    The GoatSupervisor class has unrestricted read/write access to all three memory tiers:
    - WORKING (Redis): Session-scoped storage with TTL enforcement
    - EPISODIC (ChromaDB): Semantic search across conversation history
    - LONG_TERM (Letta): Core memory blocks for agent identity/behavior
    
    Supervisor operations include:
    - Direct memory read/write during conversational turns
    - Post-execution storage of DAG results to all three tiers
    - Behavior profile persistence to Letta
    - Session initialization and finalization

DAG AGENTS (Restricted Access):
    Agents executing within the WorkflowGraph have limited memory access:
    - Can ONLY access WORKING memory (Redis) via task.memory_manager
    - CANNOT directly access ChromaDB or Letta tiers
    - Prevents memory pollution from agent-executed operations
    - Working memory is session-scoped with automatic TTL enforcement
    
    This restriction ensures:
    - Agents cannot corrupt long-term memory with transient data
    - Semantic search results remain clean and relevant
    - Core identity/behavior blocks are supervisor-controlled

PARALLEL MEMORY PIPELINE:
    During DAG execution, a concurrent pipeline handles Redis operations:
    - Runs alongside task execution without blocking
    - Stores intermediate results in working memory
    - Enables agents to read/write working context efficiently
    - ChromaDB/Letta writes happen post-execution via supervisor
    
    Implementation details:
    - asyncio.create_task() spawns non-blocking pipeline
    - Pipeline awaits completion before supervisor returns
    - Errors are logged but non-critical (don't fail execution)

TEMPERATURE SETTINGS:
    Supervisor temperature is set to 0.5 for accuracy:
    - Reduces hallucination in summaries and validations
    - Ensures consistent task result reporting
    - Critical for reliable source validation
    - Configured in config/settings.py (SupervisorConfig.temperature)

VALIDATION REQUIREMENTS:
    GOAT supervisor validates task success before reporting:
    - Checks tool_called flag for tool invocation
    - Verifies tool_name is non-empty
    - Confirms raw_output_hash proves execution
    - Validates source field matches allowed types
    - Cannot report validated=true without parameter verification

ARCHITECTURE DIAGRAM:
    ┌─────────────────────────────────────────────────────────────┐
    │                      GoatSupervisor                        │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │              Parallel Memory Pipeline                 │  │
    │  │         (Redis working memory during DAG exec)        │  │
    │  └──────────────────────────────────────────────────────┘  │
    │                           │                                 │
    │  ┌────────────────────────▼──────────────────────────────┐  │
    │  │              WorkflowGraph Execution                   │  │
    │  │    ┌─────────┐  ┌─────────┐  ┌─────────┐              │  │
    │  │    │ Agent 1 │  │ Agent 2 │  │ Agent 3 │              │  │
    │  │    │ (Redis) │  │ (Redis) │  │ (Redis) │              │  │
    │  │    └─────────┘  └─────────┘  └─────────┘              │  │
    │  └──────────────────────────────────────────────────────┘  │
    │                           │                                 │
    │         ┌─────────────────┼─────────────────┐              │
    │         ▼                 ▼                 ▼              │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
    │  │   WORKING   │  │  EPISODIC   │  │  LONG_TERM  │        │
    │  │   (Redis)   │  │ (ChromaDB)  │  │   (Letta)   │        │
    │  └─────────────┘  └─────────────┘  └─────────────┘        │
    │         ▲                 ▲                 ▲              │
    │         └─────────────────┴─────────────────┘              │
    │              Supervisor Full Access                        │
    └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations
import uuid

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from config.settings import settings
from supervisor.types import AgentRunner, AgentResult, Plan, SupervisorResult
from supervisor.registry import AgentRegistry, _build_default_registry
from supervisor.workflow import WorkflowGraph
from supervisor.planner import decompose_plan
from supervisor.critique import critique_results, synthesize_results
from supervisor.history import ConversationHistory
from supervisor.identity import conv_result, direct_response
from supervisor.classifier import classify_intent, IntentDepth
from supervisor.mem_inject import mem_turn
from supervisor.session_init import init_session
from supervisor.behavior_session import finalize_behavior
from supervisor.task_prep import prepare_tasks
from supervisor.dag_validator import validate_results
from supervisor.auditor import run_auditor

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

log = logging.getLogger("goat2.supervisor")

__all__ = ["GoatSupervisor"]

_REASON_LABELS: dict[str, str] = {
    "missing_tool_params": "tool called but parameters missing — cannot validate",
    "empty_file_read": "file tool returned no content",
    "unverified_execution": "required tool was not invoked",
    "source_violation": "tool returned disallowed source type",
    "net_error": "web search returned an error",
    "stale_memory": "memory query returned stale data",
}


def _unverified_summary(results: dict, val_statuses: list) -> str:
    """Return a factual failure message when synthesis is skipped.

    Describes only what was attempted and which tasks failed — no content is
    generated or inferred. Every word is derived from AgentResult metadata.
    GOAT cannot validate task success without verifying tool parameters.

    Args:
        results: Dictionary of task_id → AgentResult from workflow execution
        val_statuses: List of ValidationStatus from dag_validator

    Returns:
        Factual summary string describing validation failures

    MEMORY ACCESS NOTE:
        This function does not access memory directly. It operates on
        AgentResult metadata that was populated during DAG execution.
        Supervisor handles all memory writes post-execution.
    """
    parts = []
    for s in val_statuses:
        if not s.safe:
            r = results.get(s.task_id)
            role = r.role if r else s.task_id
            label = _REASON_LABELS.get(s.reason, s.reason)
            tool_ctx = f" via {r.tool_name}" if r and r.tool_name else ""
            parts.append(f"{role}{tool_ctx}: {label}")
    return ("Not available. " + "; ".join(parts) + ".") if parts else "Not available."


def _build_metadata_summary(statuses: list, audit) -> str:
    """Build a compact metadata string from validation statuses and audit report.

    Args:
        statuses: List of ValidationStatus from dag_validator
        audit: AuditReport from run_auditor()

    Returns:
        Semicolon-separated metadata string for logging

    This metadata includes:
        - Task validation status (safe/unsafe)
        - Failure reasons for unsafe tasks
        - Anomalies detected by auditor
    """
    parts = [f"task={s.task_id} safe={s.safe} reason={s.reason or 'ok'}" for s in statuses]
    parts.extend(audit.anomalies)
    return "; ".join(parts) or "ok"


class GoatSupervisor:
    """GOAT 2.0 orchestrator with unified message handling and autonomous tool selection.

    MEMORY ACCESS HIERARCHY:
    ========================
    - Supervisor: Full read/write access to Redis, ChromaDB, Letta
    - DAG Agents: Working memory (Redis) access only via task.memory_manager
    - Parallel Pipeline: Concurrent Redis operations during DAG execution

    TEMPERATURE CONFIGURATION:
    ==========================
    - Supervisor temperature: 0.5 (configured in config/settings.py)
    - Reduces hallucination and false information in summaries
    - Critical for accurate task validation and reporting

    VALIDATION REQUIREMENTS:
    ========================
    - tool_called must be True for tool-based tasks
    - tool_name must be non-empty
    - raw_output_hash must prove execution
    - source must match allowed types (file, memory, net, generated)
    """

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        """Initialize GoatSupervisor with registry and memory manager.

        Args:
            registry: AgentRegistry for looking up agent runners by role.
                     Defaults to _build_default_registry() if None.
            memory_manager: MemoryManager for three-tier memory access.
                           Required for memory operations.

        MEMORY ACCESS INITIALIZATION:
        =============================
        - memory_manager provides access to all three tiers
        - working memory (Redis) accessible to DAG agents via injection
        - episodic (ChromaDB) and long_term (Letta) are supervisor-only
        """
        self.registry = registry or _build_default_registry()
        self.memory_manager = memory_manager
        self._semaphore = asyncio.Semaphore(settings.supervisor.max_workers)
        self._verbose = settings.supervisor.verbose
        self._user_profile: str | None = None
        self._behavior_style: str = ""
        self._history: ConversationHistory | None = None
        # Parallel memory pipeline tasks for Redis operations during DAG execution
        self._memory_pipeline_tasks: list[asyncio.Task] = []

    async def _run_memory_pipeline(
        self,
        intent: str,
        results: dict[str, AgentResult],
    ) -> None:
        """Parallel memory pipeline for Redis working memory operations.

        This runs concurrently with DAG execution to handle memory read/write
        operations for the working tier (Redis) only. ChromaDB and Letta
        operations are NOT performed here — they are supervisor-only.

        MEMORY ACCESS RESTRICTIONS:
        ===========================
        - Only writes to WORKING memory (Redis)
        - ChromaDB and Letta writes happen post-execution in run()
        - Pipeline errors are logged but non-critical

        Args:
            intent: The original user intent for context
            results: DAG execution results to potentially store in working memory

        PIPELINE BEHAVIOR:
        ==================
        1. Stores DAG results in working memory for conversational access
        2. Bridges task outputs into working tier for future turns
        3. Runs non-blocking via asyncio.create_task()
        4. Errors don't fail execution (logged as warnings)
        """
        if not self.memory_manager:
            log.debug("Memory pipeline skipped: no memory_manager available")
            return

        try:
            # Store DAG results in working memory (Redis) for conversational access
            # This bridges DAG outputs into the working tier for future turns
            from supervisor.session import store_turn
            if self._history:
                await store_turn(
                    self.memory_manager,
                    len(self._history.messages),
                    intent,
                    str(results),
                )
            log.debug("Memory pipeline: stored DAG results in working memory (Redis)")
        except ImportError:
            log.debug("Memory pipeline skipped: session module not available")
        except Exception as e:
            log.warning("Memory pipeline failed (non-critical): %s", e)

    async def run(self, intent: str) -> SupervisorResult:
        """Unified message handling — all intents evaluated semantically with tool access.

        EXECUTION PATHS:
        ================
        CONVERSATIONAL: LLM with CORE_TOOLS (file/memory access) — autonomous tool selection.
        ANALYTICAL: Lightweight DAG (≤2 tasks) with tool execution.
        COMPLEX: Full DAG with planner, researcher, critic, synthesizer.

        MEMORY ACCESS FLOW:
        ===================
        1. Supervisor receives intent and classifies depth
        2. For DAG execution: parallel memory pipeline starts for Redis ops
        3. DAG agents execute with working memory access only
        4. Supervisor validates results and stores in all three tiers
        5. ChromaDB/Letta writes are supervisor-only (not DAG agents)

        VALIDATION PROCESS:
        ===================
        1. dag_validator checks source fields for all tasks
        2. auditor runs anomaly detection on results
        3. Tool parameters verified before marking validated=true
        4. Source violations logged but don't fail execution

        Args:
            intent: User message/intent to process

        Returns:
            SupervisorResult with plan, results, summary, and metadata

        TEMPERATURE NOTE:
        =================
        - Supervisor uses temperature 0.5 for accuracy
        - Configured in config/settings.py (SupervisorConfig.temperature)
        - Reduces hallucination in summaries and validations
        """
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)

        # Initialize session on first run
        if self._history is None:
            self._user_profile, self._history, self._behavior_style = await init_session(
                self.memory_manager
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent)
        depth = await classify_intent(intent)

        # CONVERSATIONAL: LLM with CORE_TOOLS — autonomous tool selection, no DAG bypass
        if depth == IntentDepth.CONVERSATIONAL:
            r = await conv_result(
                intent,
                self._history.messages,
                self._user_profile or "",
                self._history.summary,
                mem_ctx,
                t0,
                self._behavior_style,
            )
            self._history.add_assistant(r.summary)
            # Store result in WORKING memory for future conversational access
            # Supervisor-only write to all three tiers if needed
            if self.memory_manager:
                try:
                    from supervisor.session import store_turn
                    await store_turn(
                        self.memory_manager, len(self._history.messages), intent, r.summary
                    )
                except ImportError:
                    log.debug("Session storage skipped: module not available")
            return r

        # ANALYTICAL/COMPLEX: DAG execution with tool invocation
        plan_ctx = self._history.as_plan_context(
            intent, self._user_profile or "", mem_ctx
        )
        plan_ctx = f"[require_source: true]\n{plan_ctx}"
        if depth == IntentDepth.ANALYTICAL:
            plan_ctx = f"[Lightweight: ≤2 tasks]\n{plan_ctx}"

        plan = await decompose_plan(plan_ctx)
        lang = await prepare_tasks(plan.tasks, self.memory_manager, intent)

        # Start parallel memory pipeline for Redis operations during DAG execution
        # This allows concurrent memory read/write without blocking task execution
        memory_pipeline_task = asyncio.create_task(
            self._run_memory_pipeline(intent, {})
        )
        self._memory_pipeline_tasks.append(memory_pipeline_task)

        # Execute DAG with memory_manager passed for Redis working memory access
        # NOTE: DAG agents can ONLY access working tier (Redis)
        # ChromaDB and Letta are supervisor-only
        session_id = str(uuid.uuid4())
        results = await WorkflowGraph(plan.tasks).execute(
            self.registry,
            self._semaphore,
            verbose=self._verbose,
            memory_manager=self.memory_manager,
            session_id=session_id,
        )

        # Read dag_result from Redis for independent validation
        dag_verified = False
        try:
            from supervisor.session import retrieve_dag_result
            dag_detail = await retrieve_dag_result(self.memory_manager, session_id)
            if dag_detail:
                dag_verified = True
                log.info("dag_result:%s retrieved — validated=True", session_id)
            else:
                log.warning("dag_result:%s missing from Redis — validated=False", session_id)
        except Exception as e:
            log.warning("retrieve_dag_result failed: %s", e)

        # Wait for memory pipeline to complete
        try:
            await memory_pipeline_task
        except Exception as e:
            log.warning("Memory pipeline task failed (non-critical): %s", e)

        # Validate results through dag_validator
        results, val_statuses = validate_results(results)

        # Check for validation failures
        unsafe = [s for s in val_statuses if not s.safe]
        missing_src = not all(r.source for r in results.values())
        if unsafe or missing_src:
            for s in unsafe:
                log.warning(
                    "Source validation failed: task=%s reason=%s", s.task_id, s.reason
                )
            summary = _unverified_summary(results, val_statuses)
            critique = ""
        else:
            critique = await critique_results(plan_ctx, results, lang)
            summary = await synthesize_results(
                plan_ctx,
                results,
                critique,
                self._user_profile or "",
                self._behavior_style,
                lang,
                self._history.summary,
            )
            if not summary.strip():
                tools_called = sorted(
                    {r.tool_name for r in results.values() if r.tool_name}
                )
                tools_info = ", ".join(tools_called) if tools_called else "none"
                summary = (
                    f"Not available. Tools called: {tools_info}. "
                    f"No output from synthesis."
                )

        # Run auditor for anomaly detection
        audit = await run_auditor(results)

        # Build result metadata
        sources = {tid: r.source for tid, r in results.items()}
        metadata = _build_metadata_summary(val_statuses, audit)
        total = time.monotonic() - t0
        log.info(
            "Done in %.1fs — success=%s validated=%s sources=%s",
            total,
            all(r.ok for r in results.values()),
            all(r.validated for r in results.values()),
            list(sources.values()),
        )
        r = SupervisorResult(
            intent=intent,
            plan=plan,
            results=results,
            critique=critique,
            summary=summary,
            total_duration_s=total,
            sources=sources,
            metadata_summary=metadata,
        )
        self._history.add_assistant(r.summary)

        # Bridge DAG results into WORKING memory for conversational path access
        # This ensures disk contents fetched by DAG are available to subsequent turns
        # SUPERVISOR-ONLY: Writes to all three tiers (Redis, ChromaDB, Letta)
        if self.memory_manager:
            try:
                from supervisor.session import store_turn
                await store_turn(
                    self.memory_manager, len(self._history.messages), intent, r.summary
                )
            except ImportError:
                log.debug("Session storage skipped: module not available")

        return r

    async def finalize_session(self) -> None:
        """Analyze session turns, update and persist GOAT's behavior profile to Letta.

        SUPERVISOR-ONLY OPERATION:
        ==========================
        - Writes behavior profile to Letta (long-term memory)
        - DAG agents cannot perform this operation
        - Called at end of conversation session

        MEMORY ACCESS:
        ==============
        - Uses memory_manager.long_term (LettaClient)
        - Updates core memory blocks with behavior style
        - Persists across sessions
        """
        self._behavior_style = await finalize_behavior(
            self.memory_manager, self._history, self._behavior_style
        )

    def register_agent(self, role: str, runner: AgentRunner) -> None:
        """Register a pre-built async runner under a role name.

        Args:
            role: Role identifier (e.g., 'researcher', 'coder', 'critic')
            runner: Async callable matching AgentRunner protocol

        Example:
            supervisor.register_agent("researcher", ResearchAgent(spec))
        """
        self.registry.register(role, runner)

    def make_agent(
        self, role: str, model_key: str, system_prompt: str
    ) -> AgentRunner:
        """Create and register a new LLM agent from a model key + system prompt.

        Args:
            role: Role identifier for the agent
            model_key: Model specification key from config
            system_prompt: System prompt for agent behavior

        Returns:
            AgentRunner callable for supervisor execution

        MEMORY ACCESS NOTE:
        ===================
        - Created agents receive task.memory_manager during execution
        - Only working memory (Redis) accessible to agents
        - Supervisor controls persistent memory tier writes
        """
        return self.registry.make_and_register(role, model_key, system_prompt)
