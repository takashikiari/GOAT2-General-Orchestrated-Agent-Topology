"""GoatSupervisor — GOAT 2.0 top-level orchestrator. See docs/supervisor.md for full architecture."""
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
from supervisor.pipeline.critic_rerun import STRICTER_SYSTEM_PROMPTS, _rerun_failed_tasks
from supervisor.session.history import ConversationHistory
from supervisor.identity import conv_result
from supervisor.classification.classifier import classify_intent, IntentDepth
from supervisor.session.mem_inject import mem_turn
from supervisor.session.session_init import init_session
from supervisor.behavior.behavior_session import finalize_behavior
from supervisor.pipeline.task_prep import prepare_tasks
from supervisor.logging.auditor import run_auditor
from supervisor.classification.request_classifier import classify_direct_request

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import ServiceRegistry
    from agents.critique import CriticVerdict

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

_MAX_CRITIC_RETRIES: int = 2


class GoatSupervisor:
    """GOAT 2.0 orchestrator — session, tiered memory, DAG execution. See docs/supervisor.md."""

    def __init__(self, registry: "ServiceRegistry") -> None:
        """Initialize with ServiceRegistry for dependency injection."""
        log.info("GoatSupervisor: using ServiceRegistry for dependency injection")
        self.registry = registry
        self.memory_manager = registry.memory_manager
        self.agent_registry = registry
        self._settings = registry.settings
        self._semaphore = asyncio.Semaphore(self._settings.supervisor.max_workers)
        self._verbose = self._settings.supervisor.verbose
        self._user_profile: str | None = None
        self._behavior_style: str = ""
        self._history: ConversationHistory | None = None

    async def _store_and_promote(self, turn_count: int, intent: str, summary: str) -> None:
        """Store turn in working memory, auto-save to episodic tier, schedule promotion."""
        if not self.memory_manager:
            return
        try:
            from supervisor.session import store_turn
            await store_turn(self.memory_manager, turn_count, intent, summary)
            try:
                from memory.shared.hooks import auto_save_memory
                await auto_save_memory(self.memory_manager, "user_session", intent, summary)
            except Exception as e:
                log.warning("auto_save_memory failed: %s", e)
            asyncio.create_task(self._schedule_promotion(turn_count))
        except Exception as e:
            log.debug("Memory storage skipped: %s", e)

    async def _schedule_promotion(self, turn_count: int) -> None:
        """Promote conversation turns through memory tiers (background task)."""
        if not self.memory_manager:
            return
        try:
            await self.memory_manager.promote_turns(SESSION_ROLE, turn_count)
        except Exception as e:
            log.warning("Promotion task failed (non-critical): %s", e)

    async def _handle_direct_request(self, intent: str, t0: float) -> SupervisorResult | None:
        """Bypass DAG for simple memory_recent / memory_get / file_read queries."""
        classification = classify_direct_request(intent)
        if not classification:
            return None
        log.info("Direct request bypass: tool=%s confidence=%.2f query=%.60s",
                 classification.tool, classification.confidence, intent)
        try:
            from memory.memory_tools.memory_tools import MEMORY_GET
            from memory.memory_tools.memory_temporal_tools import MEMORY_RECENT
            from tools.file.file_executor import EXECUTOR
            tool, param = classification.tool, classification.extracted_param
            if tool == "memory_recent":
                result = await MEMORY_RECENT.handler()
            elif tool == "memory_get" and param:
                result = await MEMORY_GET.handler(key=param)
            elif tool == "file_read" and param:
                result = EXECUTOR.read(param)
            else:
                return None
            await self._store_and_promote(1, intent, result)
            return SupervisorResult(
                intent=intent, plan=Plan(tasks=[]), results={}, critique="", summary=result,
                total_duration_s=time.monotonic() - t0, session_id=str(uuid.uuid4()),
                sources={"direct": tool}, metadata_summary=f"direct_bypass tool={tool}",
                dag_verified=False, dag_detail="",
            )
        except Exception as e:
            log.warning("Direct request handler failed, falling back to DAG: %s", e)
            return None

    async def _run_dag(self, intent: str, t0: float, depth: IntentDepth, mem_ctx: str) -> SupervisorResult:
        """Execute ANALYTICAL or COMPLEX DAG pipeline. See docs/supervisor.md."""
        plan_ctx = self._history.as_plan_context(intent, self._user_profile or "", mem_ctx)
        plan_ctx = f"[require_source: true]\n{plan_ctx}"
        if depth == IntentDepth.ANALYTICAL:
            plan_ctx = f"[Lightweight: ≤2 tasks]\n{plan_ctx}"
        from agents.planner_decompose import decompose_plan  # lazy: agents/ cross-layer
        plan = await decompose_plan(plan_ctx, self.registry)
        lang = await prepare_tasks(plan.tasks, self.memory_manager, intent, self.registry)
        session_id = str(uuid.uuid4())
        results = await WorkflowGraph(plan.tasks).execute(
            self.agent_registry, self._semaphore,
            verbose=self._verbose, memory_manager=self.memory_manager, session_id=session_id,
        )
        dag_verified, dag_detail, validation_errors = False, "", []
        if self.memory_manager:
            try:
                from supervisor.pipeline.dag_bridge import DagBridge
                dag_result = await DagBridge(self.memory_manager).wait_for_result(session_id, timeout=120)
                if dag_result:
                    dag_detail = dag_result
                    from supervisor.pipeline.goat_validator import validate_dag_result
                    report = validate_dag_result(dag_detail, results)
                    if report.passed:
                        dag_verified = True
                        log.info("GoatValidator: passed — dag_verified=True session=%s", session_id)
                    else:
                        validation_errors = report.errors
                        log.warning("GoatValidator: failed — %s", report.errors)
                else:
                    log.warning("DagBridge: timeout session=%s — dag_verified=False", session_id)
            except Exception as e:
                log.warning("DagBridge/GoatValidator failed: %s", e)
        if not dag_verified:
            summary = ("Not available. " + "; ".join(validation_errors) + ".") if validation_errors else "UNVERIFIED"
            critique_str = ""
        else:
            from agents.critique import critique_results, synthesize_results  # lazy: agents/ cross-layer
            verdict = await critique_results(plan_ctx, results, self.registry, lang)
            retry_count = 0
            while verdict.needs_rerun and retry_count < _MAX_CRITIC_RETRIES:
                retry_count += 1
                log.info("Critic fallback attempt %d/%d: severity=%s",
                         retry_count, _MAX_CRITIC_RETRIES, verdict.severity)
                results = await _rerun_failed_tasks(
                    plan, results, self.registry, self._semaphore,
                    self.memory_manager, session_id, verdict,
                )
                verdict = await critique_results(plan_ctx, results, self.registry, lang)
            if verdict.needs_rerun:
                log.warning("Critic fallback exhausted after %d retries (severity=%s).",
                            _MAX_CRITIC_RETRIES, verdict.severity)
            critique_str = verdict.raw
            summary = await synthesize_results(
                plan_ctx, results, critique_str, self.registry,
                self._user_profile or "", self._behavior_style, lang,
                self._history.summary, dag_detail=dag_detail if dag_verified else "",
            )
            if not summary.strip():
                tools_info = ", ".join(sorted({r.tool_name for r in results.values() if r.tool_name})) or "none"
                summary = f"Not available. Tools called: {tools_info}. No output from synthesis."
        audit = await run_auditor(results)
        sources = {tid: r.source for tid, r in results.items()}
        metadata = "; ".join(audit.anomalies) or "ok"
        total = time.monotonic() - t0
        log.info("Done in %.1fs — success=%s dag_verified=%s sources=%s",
                 total, all(r.ok for r in results.values()), dag_verified, list(sources.values()))
        r = SupervisorResult(
            intent=intent, plan=plan, results=results, critique=critique_str if dag_verified else "",
            summary=summary, total_duration_s=total, session_id=session_id,
            sources=sources, metadata_summary=metadata, dag_verified=dag_verified, dag_detail=dag_detail,
        )
        self._history.add_assistant(r.summary)
        await self._store_and_promote(len(self._history.messages), intent, r.summary)
        return r

    async def run(self, intent: str) -> SupervisorResult:
        """Route intent to conversational, analytical, or complex DAG path. See docs/supervisor.md."""
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)
        if self._history is None:
            self._user_profile, self._history, self._behavior_style, _ = await init_session(
                self.memory_manager
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)
        depth = await classify_intent(intent, self.registry)
        direct_result = await self._handle_direct_request(intent, t0)
        if direct_result:
            self._history.add_assistant(direct_result.summary)
            return direct_result
        if depth == IntentDepth.CONVERSATIONAL:
            r = await conv_result(
                intent, self._history.messages, self._user_profile or "",
                self._history.summary, mem_ctx, t0, self.registry, self._behavior_style,
            )
            self._history.add_assistant(r.summary)
            await self._store_and_promote(len(self._history.messages), intent, r.summary)
            return r
        return await self._run_dag(intent, t0, depth, mem_ctx)

    async def finalize_session(self) -> None:
        """Analyze session turns and persist updated behavior profile to Letta."""
        self._behavior_style = await finalize_behavior(
            self.memory_manager, self._history, self._behavior_style, self.registry
        )

    def register_agent(self, role: str, runner: AgentRunner) -> None:
        """Register a pre-built async runner under a role name.

        Args:
            role: Role identifier (e.g., 'researcher', 'coder', 'critic')
            runner: Async callable matching AgentRunner protocol
        """
        self.agent_registry.register(role, runner)

    def make_agent(self, role: str, model_key: str, system_prompt: str) -> AgentRunner:
        """Build and register a simple LLM runner from a model key + system prompt."""
        return self.agent_registry.make_and_register(role, model_key, system_prompt)
