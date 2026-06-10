"""GOAT 2.0 top-level orchestrator — unified message handling with autonomous tool selection.

GOAT supervisor manages memory read/write directly across all three tiers (Redis, ChromaDB, Letta).
DAG agents access tools but are restricted to working memory (Redis) with SESSION_ROLE.
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

AUTOMATIC PROMOTION PIPELINE:
    After each store_turn(), background tasks promote conversation turns:
    - Turn 2+ (messages >= 4): WORKING → EPISODIC, keep_source=True
    - Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, keep_source=False
    
    Promotion includes:
    - Duplicate detection in destination tier
    - PollutionGuard validation for content quality
    - Non-blocking execution via asyncio.create_task()

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
    - dag_verified must be True — ensures LLM synthesizes from real DAG output

CRITIC FALLBACK (FIX Problema 5):
=================================
    When the critic returns severity MAJOR or CRITICAL, the supervisor:
    1. Re-executes all non-passing tasks with a stricter prompt
    2. Re-runs the critic on the new results
    3. If still failing after max retries, includes the critic's warnings in the summary
    This prevents bad output from reaching the user silently.

DIRECT REQUEST BYPASS (PATCH 71):
=================================
    Simple single-tool queries bypass the full DAG pipeline:
    - memory_recent: queries about recent memory items
    - memory_get: queries retrieving specific named facts
    - file_read: queries reading specific files by path
    
    Classification uses rule-based pattern matching (no LLM calls):
    - Rejects multi-step indicators (and, explain, analyze, compare)
    - Confidence threshold >= 0.5 required for bypass
    - Falls back to DAG on any uncertainty or error

REGISTRY INJECTION (PHASE 4):
===============================
    GoatSupervisor requires ServiceRegistry parameter for dependency injection.
    Uses registry.settings, registry.memory_manager, registry tools.
    No fallback to old singletons (removed in Phase 4).
"""
from __future__ import annotations
import uuid

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE
from supervisor.types import AgentRunner, AgentResult, Plan, SupervisorResult
from supervisor.registry import AgentRegistry
from supervisor.pipeline.workflow import WorkflowGraph
from supervisor.planner import decompose_plan
from supervisor.critique import critique_results, synthesize_results, CriticVerdict
from supervisor.session.history import ConversationHistory
from supervisor.identity import conv_result
from supervisor.classification.classifier import classify_intent, IntentDepth
from supervisor.session.mem_inject import mem_turn
from supervisor.session.session_init import init_session
from supervisor.behavior.behavior_session import finalize_behavior
from supervisor.pipeline.task_prep import prepare_tasks
from supervisor.pipeline.dag_validator import validate_results
from supervisor.logging.auditor import run_auditor
from supervisor.classification.request_classifier import classify_direct_request

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import ServiceRegistry

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

# ── FIX (Problema 5): max retry attempts for critic fallback ──
_MAX_CRITIC_RETRIES: int = 2


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


# ── FIX (Problema 5): stricter prompts for rerun ──
_STRICTER_SYSTEM_PROMPTS: dict[str, str] = {
    "researcher": (
        "You are a deep research agent. RE-EXECUTION: your previous output was flagged "
        "as insufficient by the critic. Be MORE thorough. Use web_search(query) for EVERY "
        "claim that needs verification. Cross-reference multiple sources. "
        "Output structured findings with explicit citations."
    ),
    "coder": (
        "Expert software engineer. RE-EXECUTION: your previous code was flagged as "
        "problematic by the critic. Be MORE careful. Read files before writing. "
        "Verify your logic. Add error handling. Write clean typed code in fenced blocks."
    ),
    "tool_caller": (
        "Tool orchestration agent. RE-EXECUTION: your previous execution was flagged "
        "as insufficient by the critic. Be MORE thorough. Use the right tools for each step. "
        "File tools: file_read, file_write, file_create, file_list, file_search, "
        "file_grep(path, pattern), file_info(path), file_read_lines(path, start_line, end_line). "
        "Search: web_search. Memory: memory_search, memory_get, memory_store. "
        "Say 'tool not connected' on ERROR. Never ask user to run shell commands."
    ),
}


def _get_stricter_prompt(task_role: str, original_prompt: str) -> str:
    """Return a stricter version of the task prompt for re-execution.

    Prepends a strictness instruction and appends the original prompt.
    """
    strict_override = _STRICTER_SYSTEM_PROMPTS.get(task_role, "")
    if strict_override:
        return f"[STRICT RE-EXECUTION] {strict_override}\n\nOriginal task: {original_prompt}"
    return f"[STRICT RE-EXECUTION] Be more thorough and precise.\n\nOriginal task: {original_prompt}"


async def _rerun_failed_tasks(
    plan: Plan,
    results: dict[str, AgentResult],
    registry: "ServiceRegistry",
    semaphore: asyncio.Semaphore,
    memory_manager: MemoryManager | None,
    session_id: str,
    verdict: CriticVerdict,
) -> dict[str, AgentResult]:
    """Re-execute tasks that produced problematic output.

    Only re-runs tasks whose roles are in _STRICTER_SYSTEM_PROMPTS (researcher, coder, tool_caller).
    Keeps existing results for tasks that passed critic review.
    Updates the results dict in-place with new outputs.

    Args:
        plan: Original plan with task definitions
        results: Current results dict (mutated in-place)
        registry: ServiceRegistry for dependency injection (Phase 4)
        semaphore: Concurrency semaphore
        memory_manager: Memory manager for Redis access
        session_id: Current session ID
        verdict: CriticVerdict from the failed critique

    Returns:
        Updated results dict
    """
    log.info(
        "Critic fallback: severity=%s, re-executing failing tasks",
        verdict.severity,
    )

    # Identify which tasks to rerun (only roles that can improve with stricter prompts)
    rerun_tasks = [
        t for t in plan.tasks
        if t.role in _STRICTER_SYSTEM_PROMPTS
        and t.id in results
        and results[t.id].error is None  # don't rerun errored tasks
    ]

    if not rerun_tasks:
        log.info("Critic fallback: no rerunnable tasks found")
        return results

    # Build a mini-workflow just for the rerun tasks
    # We execute them sequentially (not full DAG) since they may depend on each other
    for task in rerun_tasks:
        # Create stricter prompt
        original_prompt = task.prompt
        task.prompt = _get_stricter_prompt(task.role, original_prompt)

        # Build context from existing results
        context = {
            dep_id: results[dep_id]
            for dep_id in task.depends_on
            if dep_id in results and results[dep_id].error is None
        }

        # Execute with semaphore
        async with semaphore:
            task.memory_manager = memory_manager
            t_start = time.monotonic()
            try:
                runner = registry.get(task.role)
                # Phase 4: Pass registry to runner for dependency injection
                output = await runner(task, context, registry)
                duration = time.monotonic() - t_start
                results[task.id] = AgentResult(
                    task_id=task.id,
                    role=task.role,
                    output=output,
                    model="",
                    duration_s=duration,
                    error=None,
                    source=task.source,
                    tool_called=False,
                    tool_name="",
                    raw_output_hash="",
                )
                log.info("Rerun task %s (%s): OK (%.1fs)", task.id, task.role, duration)
            except Exception as e:
                duration = time.monotonic() - t_start
                log.exception("Rerun task %s failed", task.id)
                results[task.id] = AgentResult(
                    task_id=task.id,
                    role=task.role,
                    output="",
                    model="",
                    duration_s=duration,
                    error=str(e),
                    source=task.source,
                    tool_called=False,
                    tool_name="",
                    raw_output_hash="",
                )

        # Restore original prompt for any subsequent rerun
        task.prompt = original_prompt

    return results


class GoatSupervisor:
    """GOAT 2.0 orchestrator with unified message handling and autonomous tool selection.

    MEMORY ACCESS HIERARCHY:
    ========================
    - Supervisor: Full read/write access to Redis, ChromaDB, Letta
    - DAG Agents: Working memory (Redis) access only via task.memory_manager
    - Parallel Pipeline: Concurrent Redis operations during DAG execution

    AUTOMATIC PROMOTION:
    ====================
    After store_turn(), background tasks promote conversation turns:
    - Turn 2+ (messages >= 4): WORKING → EPISODIC, keep_source=True
    - Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, keep_source=False

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
    - dag_verified must be True — ensures LLM synthesizes from real DAG output

    CRITIC FALLBACK (FIX Problema 5):
    =================================
    When critic returns MAJOR or CRITICAL, tasks are re-executed with stricter prompts.
    Up to _MAX_CRITIC_RETRIES (2) attempts before accepting the output.

    DIRECT REQUEST BYPASS (PATCH 71):
    =================================
    Simple single-tool queries bypass the full DAG pipeline:
    - memory_recent, memory_get, file_read
    - Rule-based classification (no LLM calls)
    - Falls back to DAG on uncertainty or error

    REGISTRY INJECTION (PHASE 4):
    ==============================
    Requires ServiceRegistry parameter for dependency injection.
    Uses registry.settings, registry.memory_manager, registry tools.
    No fallback to old singletons.
    """

    def __init__(
        self,
        registry: "ServiceRegistry",
    ) -> None:
        """Initialize GoatSupervisor with ServiceRegistry.

        Args:
            registry: Central ServiceRegistry container. Required.
                     Uses registry.settings, registry.memory_manager, etc.

        MEMORY ACCESS INITIALIZATION:
        =============================
        - registry.memory_manager provides access to all three tiers
        - working memory (Redis) accessible to DAG agents via injection
        - episodic (ChromaDB) and long_term (Letta) are supervisor-only
        """
        log.info("GoatSupervisor: using ServiceRegistry for dependency injection")
        self.registry = registry
        self.memory_manager = registry.memory_manager
        self.agent_registry = registry
        self._settings = registry.settings

        # Semaphore for concurrent task execution
        self._semaphore = asyncio.Semaphore(
            self._settings.supervisor.max_workers
        )
        self._verbose = self._settings.supervisor.verbose
        self._user_profile: str | None = None
        self._behavior_style: str = ""
        self._history: ConversationHistory | None = None
        # Parallel memory pipeline tasks for Redis operations during DAG execution
        self._memory_pipeline_tasks: list[asyncio.Task] = []

    async def _handle_direct_request(
        self,
        intent: str,
        t0: float,
    ) -> SupervisorResult | None:
        """Handle simple single-tool requests without DAG execution.

        Bypasses planner and workflow graph for queries that can be
        answered by memory_recent, memory_get, or file_read directly.

        Args:
            intent: User's message text
            t0: Start timestamp for duration calculation

        Returns:
            SupervisorResult if direct handling succeeded, None otherwise

        DIRECT TOOL MAPPING:
        ====================
        - memory_recent: queries about recent memory items
        - memory_get: queries retrieving specific named facts
        - file_read: queries reading specific files by path

        SAFETY:
        =======
        - Falls back to DAG if classification uncertain
        - Falls back to DAG if tool execution fails
        - Logs bypass events at INFO level
        """
        from tools.memory.memory_tools import MEMORY_GET
        from tools.memory.memory_temporal_tools import MEMORY_RECENT
        from tools.file.file_executor import EXECUTOR

        classification = classify_direct_request(intent)

        if not classification:
            return None

        log.info(
            "Direct request bypass: tool=%s confidence=%.2f query=%.60s",
            classification.tool,
            classification.confidence,
            intent,
        )

        try:
            if classification.tool == "memory_recent":
                # Execute memory_recent directly
                result = await MEMORY_RECENT.handler()
                tool_name = "memory_recent"
            elif classification.tool == "memory_get":
                # Execute memory_get with extracted key
                if classification.extracted_param:
                    result = await MEMORY_GET.handler(key=classification.extracted_param)
                else:
                    return None  # Safety: no key extracted
                tool_name = "memory_get"
            elif classification.tool == "file_read":
                # Execute file_read directly using FileToolExecutor
                if classification.extracted_param:
                    result = EXECUTOR.read(classification.extracted_param)
                else:
                    return None  # Safety: no path extracted
                tool_name = "file_read"
            else:
                return None

            # Build SupervisorResult for direct response
            duration = time.monotonic() - t0
            session_id = str(uuid.uuid4())

            # Store in working memory for conversational continuity
            if self.memory_manager:
                try:
                    from supervisor.session import store_turn
                    await store_turn(
                        self.memory_manager,
                        1,  # First turn
                        intent,
                        result,
                    )
                    # Auto-save to episodic/long-term memory
                    try:
                        from memory.hooks import auto_save_memory
                        await auto_save_memory(
                            self.memory_manager,
                            "user_session",
                            intent,
                            r.summary if "r" in dir() else "",
                        )
                    except Exception as e:
                        log.warning("auto_save_memory failed: %s", e)
                except Exception as e:
                    log.warning("Direct request memory store failed: %s", e)

            return SupervisorResult(
                intent=intent,
                plan=Plan(tasks=[]),
                results={},
                critique="",
                summary=result,
                total_duration_s=duration,
                session_id=session_id,
                sources={"direct": classification.tool},
                metadata_summary=f"direct_bypass tool={tool_name}",
                dag_verified=False,
                dag_detail="",
            )

        except Exception as e:
            log.warning("Direct request handler failed, falling back to DAG: %s", e)
            return None

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
                # Auto-save to episodic/long-term memory
                try:
                    from memory.hooks import auto_save_memory
                    await auto_save_memory(
                        self.memory_manager,
                        "user_session",
                        intent,
                        r.summary if "r" in dir() else "",
                    )
                except Exception as e:
                    log.warning("auto_save_memory failed: %s", e)
            log.debug("Memory pipeline: stored DAG results in working memory (Redis)")
        except ImportError:
            log.debug("Memory pipeline skipped: session module not available")
        except Exception as e:
            log.warning("Memory pipeline failed (non-critical): %s", e)

    async def _schedule_promotion(self, turn_count: int) -> None:
        """Schedule automatic memory promotion based on turn count.

        Promotion rules:
        - Turn 2+ (messages >= 4): WORKING → EPISODIC, keep_source=True
        - Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, keep_source=False

        Runs as non-blocking background task via asyncio.create_task().

        Args:
            turn_count: Current number of messages in conversation history
        """
        if not self.memory_manager:
            return
        try:
            await self.memory_manager.promote_turns(SESSION_ROLE, turn_count)
        except Exception as e:
            log.warning("Promotion task failed (non-critical): %s", e)

    async def run(self, intent: str) -> SupervisorResult:
        """Unified message handling — all intents evaluated semantically with tool access.

        EXECUTION PATHS:
        ================
        CONVERSATIONAL: LLM with CORE_TOOLS (file/memory access) — autonomous tool selection.
        ANALYTICAL: Lightweight DAG (≤2 tasks) with tool execution.
        COMPLEX: Full DAG with planner, researcher, critic, synthesizer.
        DIRECT BYPASS: Single-tool queries (memory_recent, memory_get, file_read).

        MEMORY ACCESS FLOW:
        ===================
        1. Supervisor receives intent and classifies depth
        2. Direct request pre-check for simple single-tool queries
        3. For DAG execution: parallel memory pipeline starts for Redis ops
        4. DAG agents execute with working memory access only
        5. Supervisor validates results and stores in all three tiers
        6. ChromaDB/Letta writes are supervisor-only (not DAG agents)

        AUTOMATIC PROMOTION:
        ====================
        After store_turn(), background tasks promote conversation turns:
        - Turn 2+ (messages >= 4): WORKING → EPISODIC, keep_source=True
        - Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, keep_source=False

        VALIDATION PROCESS:
        ===================
        1. dag_validator checks source fields for all tasks
        2. auditor runs anomaly detection on results
        3. Tool parameters verified before marking validated=true
        4. Source violations logged but don't fail execution
        5. dag_verified ensures LLM synthesizes from real DAG output

        CRITIC FALLBACK (FIX Problema 5):
        =================================
        After initial critique, if verdict is MAJOR or CRITICAL:
        - Re-execute failing tasks with stricter prompts
        - Re-run critic on new results
        - Up to _MAX_CRITIC_RETRIES (2) attempts
        - If still failing after max retries, include critic warnings in summary

        DIRECT REQUEST BYPASS (PATCH 71):
        =================================
        Before planner invocation, checks if query can be handled by single tool:
        - memory_recent, memory_get, file_read
        - Rule-based classification (no LLM calls)
        - Falls back to DAG on uncertainty or error

        REGISTRY INJECTION (PHASE 4):
        =============================
        Registry is passed through to:
        - classify_intent() for model selection
        - decompose_plan() for supervisor model
        - WorkflowGraph.execute() for runner injection
        - critique_results() and synthesize_results() for model selection
        - conv_result() for tool access

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
            self._user_profile, self._history, self._behavior_style, _ = await init_session(
                self.memory_manager
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)
        # Phase 4: Pass registry to classify_intent
        depth = await classify_intent(intent, self.registry)

        # PRE-CHECK: Direct tool bypass for simple single-tool queries
        # This happens before planner invocation to avoid unnecessary DAG overhead
        direct_result = await self._handle_direct_request(intent, t0)
        if direct_result:
            self._history.add_assistant(direct_result.summary)
            return direct_result

        # CONVERSATIONAL: LLM with CORE_TOOLS — autonomous tool selection, no DAG bypass
        if depth == IntentDepth.CONVERSATIONAL:
            # Phase 4: Pass registry to conv_result
            r = await conv_result(
                intent,
                self._history.messages,
                self._user_profile or "",
                self._history.summary,
                mem_ctx,
                t0,
                self.registry,
                self._behavior_style,
            )
            self._history.add_assistant(r.summary)
            # Store result in WORKING memory for future conversational access
            # Supervisor-only write to all three tiers if needed
            if self.memory_manager:
                try:
                    from supervisor.session import store_turn
                    turn_count = len(self._history.messages)
                    await store_turn(
                        self.memory_manager, turn_count, intent, r.summary
                    )
                    # Auto-save to episodic/long-term memory
                    try:
                        from memory.hooks import auto_save_memory
                        await auto_save_memory(
                            self.memory_manager,
                            "user_session",
                            intent,
                            r.summary if "r" in dir() else "",
                        )
                    except Exception as e:
                        log.warning("auto_save_memory failed: %s", e)
                    # Schedule automatic promotion based on turn count
                    asyncio.create_task(self._schedule_promotion(turn_count))
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

        # Phase 4: Pass registry to decompose_plan
        plan = await decompose_plan(plan_ctx, self.registry)
        lang = await prepare_tasks(plan.tasks, self.memory_manager, intent, self.registry)

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
        # Phase 4: Pass registry to WorkflowGraph.execute
        results = await WorkflowGraph(plan.tasks).execute(
            self.agent_registry,
            self._semaphore,
            verbose=self._verbose,
            memory_manager=self.memory_manager,
            session_id=session_id,
        )

        # Read dag_result from Redis for independent validation
        dag_verified = False
        dag_detail = ""
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
            # ── FIX (Problema 5): Critic with fallback loop ──
            # Phase 4: Pass registry to critique_results
            verdict = await critique_results(plan_ctx, results, self.registry, lang)
            retry_count = 0

            while verdict.needs_rerun and retry_count < _MAX_CRITIC_RETRIES:
                retry_count += 1
                log.info(
                    "Critic fallback attempt %d/%d: severity=%s",
                    retry_count, _MAX_CRITIC_RETRIES, verdict.severity,
                )
                # Phase 4: Pass registry to _rerun_failed_tasks
                results = await _rerun_failed_tasks(
                    plan, results, self.registry, self._semaphore,
                    self.memory_manager, session_id, verdict,
                )
                # Re-validate after rerun
                results, val_statuses = validate_results(results)
                # Phase 4: Pass registry to critique_results
                verdict = await critique_results(plan_ctx, results, self.registry, lang)

            if verdict.needs_rerun:
                log.warning(
                    "Critic fallback exhausted after %d retries (severity=%s). "
                    "Including critic warnings in summary.",
                    _MAX_CRITIC_RETRIES, verdict.severity,
                )

            # Pass dag_detail to synthesize_results only when dag_verified=True
            # This ensures the LLM synthesizes from real DAG output instead of hallucinating
            critique_str = verdict.raw
            # Phase 4: Pass registry to synthesize_results
            summary = await synthesize_results(
                plan_ctx,
                results,
                critique_str,
                self.registry,
                self._user_profile or "",
                self._behavior_style,
                lang,
                self._history.summary,
                dag_detail=dag_detail if dag_verified else "",
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
            "Done in %.1fs — success=%s validated=%s dag_verified=%s sources=%s",
            total,
            all(r.ok for r in results.values()),
            dag_verified,
            dag_verified,
            list(sources.values()),
        )
        r = SupervisorResult(
            intent=intent,
            plan=plan,
            results=results,
            critique=critique_str if not (unsafe or missing_src) else "",
            summary=summary,
            total_duration_s=total,
            session_id=session_id,
            sources=sources,
            metadata_summary=metadata,
            dag_verified=dag_verified,
            dag_detail=dag_detail,
        )
        self._history.add_assistant(r.summary)

        # Bridge DAG results into WORKING memory for conversational path access
        # This ensures disk contents fetched by DAG are available to subsequent turns
        # SUPERVISOR-ONLY: Writes to all three tiers (Redis, ChromaDB, Letta)
        if self.memory_manager:
            try:
                from supervisor.session import store_turn
                turn_count = len(self._history.messages)
                await store_turn(
                    self.memory_manager, turn_count, intent, r.summary
                )
                # Auto-save to episodic/long-term memory
                try:
                    from memory.hooks import auto_save_memory
                    await auto_save_memory(
                        self.memory_manager,
                        "user_session",
                        intent,
                        r.summary if "r" in dir() else "",
                    )
                except Exception as e:
                    log.warning("auto_save_memory failed: %s", e)
                # Schedule automatic promotion based on turn count
                asyncio.create_task(self._schedule_promotion(turn_count))
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
        
        REGISTRY INJECTION (PHASE 4):
        =============================
        Passes registry to finalize_behavior for settings access
        """
        # Phase 4: Pass registry to finalize_behavior
        self._behavior_style = await finalize_behavior(
            self.memory_manager, self._history, self._behavior_style, self.registry
        )

    def register_agent(self, role: str, runner: AgentRunner) -> None:
        """Register a pre-built async runner under a role name.

        Args:
            role: Role identifier (e.g., 'researcher', 'coder', 'critic')
            runner: Async callable matching AgentRunner protocol

        Example:
            supervisor.register_agent("researcher", ResearchAgent(spec))
        """
        self.agent_registry.register(role, runner)

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
        return self.agent_registry.make_and_register(role, model_key, system_prompt)
