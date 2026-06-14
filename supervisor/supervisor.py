"""GoatSupervisor — GOAT 2.0 top-level orchestrator. See docs/supervisor.md for full architecture."""
from __future__ import annotations
import uuid

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from supervisor.types import AgentRunner, Plan, SupervisorResult
from supervisor.session.history import ConversationHistory
from supervisor.identity import conv_result
from supervisor.classification.classifier import IntentDepth, classify_intent
from supervisor.session.mem_inject import mem_turn
from supervisor.session.session_init import init_session
from supervisor.behavior.behavior_session import finalize_behavior
from supervisor.session.turn_persistence import store_and_promote
from supervisor.session.routing_state import (
    pop_pending_dag,
    get_previous_routing,
    set_previous_routing,
    clear_previous_routing,
    check_disagreement,
    store_routing_correction,
)
from supervisor.pipeline.gates import check_intent_clarity_gate, validate_dag_prompt_gate
from supervisor.pipeline.intent_clarity import CLARITY_THRESHOLD, CLARITY_CONFIDENT

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import ServiceRegistry
    from agents.critique import CriticVerdict
    from supervisor.pipeline.goat_enrichment import GoatDecision

log = logging.getLogger("goat2.supervisor")

__all__ = ["GoatSupervisor"]

# Capability summary written into DAG instructions so DAG knows what agents can do.
_DAG_CAPABILITIES_SUMMARY: str = (
    "tool_caller: file_read, file_write, file_create, file_list, file_search, file_grep, "
    "memory_recent, memory_get, memory_store, memory_search; "
    "researcher: web_search, memory_search; "
    "coder: file_read, file_write, file_create, shell(read-only); "
    "critic: memory_recent, memory_get(read-only); "
    "summarizer: memory_recent(read-only)"
)

_FALLBACK_CLARIFICATION = "Could you provide more details about what you'd like me to do?"


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
        self._session_id: str = str(uuid.uuid4())

    async def _run_dag(
        self, intent: str, t0: float, depth: IntentDepth, mem_ctx: str,
        decision: "GoatDecision | None" = None,
    ) -> SupervisorResult:
        """Thin wrapper over the extracted pipeline module.

        Threads GOAT's enrichment decision through to the DAG pipeline. When
        ``decision`` is None (e.g. the pending-DAG fast path), the pipeline
        enriches the intent itself so the Prompter always receives a decision.
        """
        from supervisor.pipeline.dag_execution import run_dag_pipeline
        log.debug("_run_dag: depth=%s decision=%s", depth.value, "provided" if decision else "none")
        return await run_dag_pipeline(self, intent, t0, depth, mem_ctx, decision)

    async def _enrich_intent(self, intent: str, mem_ctx: str) -> "GoatDecision":
        """GOAT decides — enrich raw intent into a complete GoatDecision.

        This is the *decide* stage of GOAT decides → Prompter formats → DAG
        executes. Delegates to the enrichment module's single LLM call.
        """
        from supervisor.pipeline.goat_enrichment import enrich_intent
        from supervisor.classification.classifier_prompt import format_history
        history_text = format_history(self._history.messages) if self._history else ""
        return await enrich_intent(intent, mem_ctx, history_text, self.registry)

    async def _execute_with_depth(
        self, intent: str, t0: float, depth: IntentDepth, mem_ctx: str
    ) -> SupervisorResult:
        """Execute with a specific routing depth (after correction)."""
        await set_previous_routing(self.memory_manager, self._session_id, depth)
        if depth == IntentDepth.CONVERSATIONAL:
            r = await conv_result(
                intent, self._history.messages, self._user_profile or "",
                self._history.summary, mem_ctx, t0, self.registry, self._behavior_style,
                goat_session_id=self._session_id,
            )
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r
        # COMPLEX or ANALYTICAL: run through DAG pipeline with gates
        if self.memory_manager:
            try:
                from supervisor.session.session import write_dag_instructions
                await write_dag_instructions(
                    self.memory_manager, self._session_id,
                    intent, mem_ctx, _DAG_CAPABILITIES_SUMMARY,
                )
            except Exception as e:
                log.warning("write_dag_instructions failed: %s", e)

        # Gate 1 — Intent clarity (scored: <0.5 blocks; 0.5–0.79 proceeds with warning)
        clarity = await check_intent_clarity_gate(self, intent, mem_ctx)
        log.debug("GOAT: clarity_score=%.2f clear=%s", clarity.clarity_score, clarity.clear)
        if clarity.clarity_score < CLARITY_THRESHOLD:
            log.info("GOAT: intent unclear — score=%.2f question=%.80s",
                     clarity.clarity_score, clarity.clarification_question)
            r = self._clarification_result(intent, t0, clarity.clarification_question)
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r
        if clarity.clarity_score < CLARITY_CONFIDENT:
            log.warning("GOAT: intent partially clear — score=%.2f, proceeding from context. missing=%s",
                        clarity.clarity_score, clarity.missing)

        # GOAT decides — enrich the raw intent into a complete GoatDecision once,
        # then reuse it for both the validation gate and DAG execution.
        decision = await self._enrich_intent(intent, mem_ctx)

        # Gate 2 — DagPrompt validation (Prompter formats the decision, then validates)
        dag_validity = await validate_dag_prompt_gate(self, decision, intent, mem_ctx)
        if not dag_validity.clear:
            log.info("GOAT: dag_prompt invalid — question=%.80s", dag_validity.clarification_question)
            r = self._clarification_result(intent, t0, dag_validity.clarification_question)
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r

        return await self._run_dag(intent, t0, depth, mem_ctx, decision)

    def _clarification_result(self, intent: str, t0: float, question: str) -> SupervisorResult:
        """Build a SupervisorResult that surfaces a clarification question to the user."""
        return SupervisorResult(
            intent=intent,
            plan=Plan(tasks=[]),
            results={},
            critique="",
            summary=question or _FALLBACK_CLARIFICATION,
            sources={"conv": "generated"},
            total_duration_s=time.monotonic() - t0,
        )

    async def run(self, intent: str) -> SupervisorResult:
        """Route intent: classify_intent decides conversational vs DAG.

        FLOW:
        1. init session → add user turn → memory turn
        2. check for routing disagreement with previous decision
        3. classify_intent → CONVERSATIONAL or COMPLEX
        4. CONVERSATIONAL: conv_result; if start_dag called, fire background DAG
        5. COMPLEX: write instructions → clarity gate → prompt validation gate → DAG
        """
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)
        if self._history is None:
            self._user_profile, self._history, self._behavior_style, _ = await init_session(
                self.memory_manager
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)

        # Check for pending DAG from start_dag tool call
        pending_dag_session = await pop_pending_dag(self.memory_manager, self._session_id)
        if pending_dag_session:
            log.info("GOAT: pending DAG found session=%s — firing DAG", pending_dag_session)
            return await self._run_dag(intent, t0, IntentDepth.COMPLEX, mem_ctx)

        # Check for routing disagreement with previous decision before classifying
        previous_routing = await get_previous_routing(self.memory_manager, self._session_id)
        if previous_routing:
            is_disagree, wanted = await check_disagreement(self.registry, intent, previous_routing)
            if is_disagree:
                log.info("routing correction: was=%s wanted=%s", previous_routing, wanted)
                # Store the correction for future learning
                await store_routing_correction(self.registry, intent, previous_routing, wanted)
                # Apply the correction: override the classification
                if wanted == "complex":
                    depth = IntentDepth.COMPLEX
                elif wanted == "conversational":
                    depth = IntentDepth.CONVERSATIONAL
                else:
                    depth = IntentDepth.ANALYTICAL
                # Clear previous routing and continue with the corrected depth
                await clear_previous_routing(self.memory_manager, self._session_id)
                return await self._execute_with_depth(intent, t0, depth, mem_ctx)

        # Single classifier — classify_intent gathers active DAGs and context internally.
        depth = await classify_intent(
            intent, self.registry, self._history, session_id=self._session_id,
        )
        log.info("classify_intent: intent=%.80s → %s", intent, depth.value)
        return await self._execute_with_depth(intent, t0, depth, mem_ctx)

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

    async def pause_dag(self, session_id: str) -> None:
        """Write "pause" to dag:<session_id>:control — DAG halts after its current wave."""
        from supervisor.pipeline.dag_control import write_dag_control
        await write_dag_control(self.memory_manager, session_id, "pause")

    async def resume_dag(self, session_id: str) -> None:
        """Write "run" to dag:<session_id>:control — resumes a paused DAG."""
        from supervisor.pipeline.dag_control import write_dag_control
        await write_dag_control(self.memory_manager, session_id, "run")

    async def stop_dag(self, session_id: str) -> None:
        """Write "stop" to dag:<session_id>:control — DAG terminates after current wave."""
        from supervisor.pipeline.dag_control import write_dag_control
        await write_dag_control(self.memory_manager, session_id, "stop")

    async def get_dag_updates(self, session_id: str) -> dict | None:
        """Read dag:<session_id>:progress from working memory.

        Returns:
            Progress dict (wave, total_waves, completed_tasks, status) or None.
        """
        from supervisor.pipeline.dag_awareness import read_dag_progress
        return await read_dag_progress(self.registry, session_id)
