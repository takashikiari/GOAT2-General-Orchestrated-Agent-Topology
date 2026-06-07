"""GOAT 2.0 top-level orchestrator — unified message handling with autonomous tool selection.

GOAT supervisor manages memory read/write directly across all three tiers (Redis, ChromaDB, Letta).
DAG agents access tools but are restricted to working memory (Redis) with role="user_session".
GOAT validates task success by checking tool parameters — never reports validated without verification.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from config.settings import settings
from supervisor.types import AgentRunner, Plan, SupervisorResult
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
    "missing_tool_params":  "tool called but parameters missing — cannot validate",
    "empty_file_read":      "file tool returned no content",
    "unverified_execution": "required tool was not invoked",
    "source_violation":     "tool returned disallowed source type",
    "net_error":            "web search returned an error",
    "stale_memory":         "memory query returned stale data",
}


def _unverified_summary(results: dict, val_statuses: list) -> str:
    """Return a factual failure message when synthesis is skipped.

    Describes only what was attempted and which tasks failed — no content is
    generated or inferred. Every word is derived from AgentResult metadata.
    GOAT cannot validate task success without verifying tool parameters.
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
    """Build a compact metadata string from validation statuses and audit report."""
    parts = [f"task={s.task_id} safe={s.safe} reason={s.reason or 'ok'}" for s in statuses]
    parts.extend(audit.anomalies)
    return "; ".join(parts) or "ok"


class GoatSupervisor:
    """GOAT 2.0 orchestrator with unified message handling and autonomous tool selection.

    GOAT supervisor manages memory read/write directly across all three tiers.
    DAG agents access tools but are restricted to working memory (Redis).
    GOAT validates task success by checking tool parameters — never reports validated without verification.
    """

    def __init__(
        self,
        registry:       AgentRegistry | None = None,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.registry        = registry or _build_default_registry()
        self.memory_manager  = memory_manager
        self._semaphore      = asyncio.Semaphore(settings.supervisor.max_workers)
        self._verbose        = settings.supervisor.verbose
        self._user_profile:   str | None          = None
        self._behavior_style: str                 = ""
        self._history: ConversationHistory | None = None

    async def run(self, intent: str) -> SupervisorResult:
        """Unified message handling — all intents evaluated semantically with tool access.

        CONVERSATIONAL: LLM with CORE_TOOLS (file/memory access) — autonomous tool selection.
        ANALYTICAL: Lightweight DAG (≤2 tasks) with tool execution.
        COMPLEX: Full DAG with planner, researcher, critic, synthesizer.

        GOAT supervisor manages memory directly. DAG agents restricted to working tier (Redis).
        GOAT validates tool parameters before marking tasks successful.
        """
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)

        if self._history is None:
            self._user_profile, self._history, self._behavior_style = await init_session(self.memory_manager)
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent)
        depth   = await classify_intent(intent)

        # CONVERSATIONAL: LLM with CORE_TOOLS — autonomous tool selection, no DAG bypass
        if depth == IntentDepth.CONVERSATIONAL:
            r = await conv_result(
                intent, self._history.messages, self._user_profile or "",
                self._history.summary, mem_ctx, t0, self._behavior_style,
            )
            self._history.add_assistant(r.summary)
            # Store result in WORKING memory for future conversational access
            if self.memory_manager:
                from supervisor.session import store_turn
                await store_turn(self.memory_manager, len(self._history.messages), intent, r.summary)
            return r

        # ANALYTICAL/COMPLEX: DAG execution with tool invocation
        plan_ctx = self._history.as_plan_context(intent, self._user_profile or "", mem_ctx)
        plan_ctx = f"[require_source: true]\n{plan_ctx}"
        if depth == IntentDepth.ANALYTICAL:
            plan_ctx = f"[Lightweight: ≤2 tasks]\n{plan_ctx}"

        plan = await decompose_plan(plan_ctx)
        lang  = await prepare_tasks(plan.tasks, self.memory_manager, intent)
        results = await WorkflowGraph(plan.tasks).execute(
            self.registry, self._semaphore, verbose=self._verbose,
        )

        results, val_statuses = validate_results(results)

        unsafe = [s for s in val_statuses if not s.safe]
        missing_src = not all(r.source for r in results.values())
        if unsafe or missing_src:
            for s in unsafe:
                log.warning("Source validation failed: task=%s reason=%s", s.task_id, s.reason)
            summary  = _unverified_summary(results, val_statuses)
            critique = ""
        else:
            critique = await critique_results(plan_ctx, results, lang)
            summary  = await synthesize_results(
                plan_ctx, results, critique, self._user_profile or "",
                self._behavior_style, lang, self._history.summary,
            )
            if not summary.strip():
                tools_called = sorted({r.tool_name for r in results.values() if r.tool_name})
                tools_info   = ", ".join(tools_called) if tools_called else "none"
                summary = f"Not available. Tools called: {tools_info}. No output from synthesis."

        audit = await run_auditor(results)

        sources  = {tid: r.source for tid, r in results.items()}
        metadata = _build_metadata_summary(val_statuses, audit)
        total    = time.monotonic() - t0
        log.info("Done in %.1fs — success=%s validated=%s sources=%s", total,
                 all(r.ok for r in results.values()), all(r.validated for r in results.values()), list(sources.values()))
        r = SupervisorResult(
            intent=intent, plan=plan, results=results,
            critique=critique, summary=summary, total_duration_s=total,
            sources=sources, metadata_summary=metadata,
        )
        self._history.add_assistant(r.summary)

        # Bridge DAG results into WORKING memory for conversational path access
        # This ensures disk contents fetched by DAG are available to subsequent turns
        if self.memory_manager:
            from supervisor.session import store_turn
            await store_turn(self.memory_manager, len(self._history.messages), intent, r.summary)

        return r

    async def finalize_session(self) -> None:
        """Analyze session turns, update and persist GOAT's behavior profile to Letta."""
        self._behavior_style = await finalize_behavior(
            self.memory_manager, self._history, self._behavior_style)

    def register_agent(self, role: str, runner: AgentRunner) -> None:
        """Register a pre-built async runner under a role name."""
        self.registry.register(role, runner)

    def make_agent(self, role: str, model_key: str, system_prompt: str) -> AgentRunner:
        """Create and register a new LLM agent from a model key + system prompt."""
        return self.registry.make_and_register(role, model_key, system_prompt)
