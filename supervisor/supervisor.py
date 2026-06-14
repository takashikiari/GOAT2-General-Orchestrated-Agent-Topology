"""GoatSupervisor — GOAT 2.0 top-level orchestrator. See docs/supervisor.md for full architecture.

Single-call architecture: one GOAT decision LLM call (``goat_decision.decide``)
replaces the former 6-call routing pipeline. Middleware only builds context (no
LLM). Based on the decision's action the supervisor either replies directly
(tool-enabled), asks a clarification, or runs the DAG (specialized agent LLMs).
"""
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
from supervisor.session.routing_state import pop_pending_dag

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import ServiceRegistry
    from agents.critique import CriticVerdict
    from supervisor.pipeline.goat_decision import GoatDecision

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
    """GOAT 2.0 orchestrator — session, tiered memory, single-call routing, DAG execution."""

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
        self, intent: str, t0: float, mem_ctx: str, dag_instructions: str = "",
    ) -> SupervisorResult:
        """Run the DAG pipeline with GOAT's self-contained ``dag_instructions``.

        The instructions (already decided by the single GOAT call) become the
        planner objective; the DAG's specialized agents do the rest.
        """
        from supervisor.pipeline.dag_execution import run_dag_pipeline
        log.debug("_run_dag: instructions=%.80s", dag_instructions or intent)
        return await run_dag_pipeline(self, intent, t0, mem_ctx, dag_instructions or intent)

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

    async def _reply_direct(self, intent: str, t0: float, mem_ctx: str) -> SupervisorResult:
        """Generate a tool-enabled conversational reply (memory/web available)."""
        r = await conv_result(
            intent, self._history.messages, self._user_profile or "",
            self._history.summary, mem_ctx, t0, self.registry, self._behavior_style,
            goat_session_id=self._session_id,
        )
        self._history.add_assistant(r.summary)
        await store_and_promote(self, len(self._history.messages), intent, r.summary)
        return r

    async def _build_context(self, intent: str, mem_ctx: str):
        """Build all decision context — pure, NO LLM. Returns (goat_ctx, clarity_ctx, hints)."""
        from supervisor.pipeline.goat_enrichment import build_goat_context
        from supervisor.pipeline.intent_clarity import build_clarity_context
        from supervisor.pipeline.behavioral_learning import recall_corrections
        from supervisor.classification.classifier_prompt import format_dialogue
        goat_ctx = build_goat_context(self.registry, mem_ctx)
        history_text = format_dialogue(self._history.messages) if self._history else ""
        clarity_ctx = build_clarity_context(history_text, mem_ctx)
        hints = await recall_corrections(self.registry, limit=3)
        return goat_ctx, clarity_ctx, hints

    async def _dispatch(
        self, intent: str, t0: float, mem_ctx: str, decision: "GoatDecision",
    ) -> SupervisorResult:
        """Execute GOAT's decision: dag → pipeline, clarify → question, direct → reply."""
        if decision.action == "dag":
            if self.memory_manager:
                try:
                    from supervisor.session.session import write_dag_instructions
                    await write_dag_instructions(
                        self.memory_manager, self._session_id,
                        decision.dag_instructions or intent, mem_ctx, _DAG_CAPABILITIES_SUMMARY,
                    )
                except Exception as e:
                    log.warning("write_dag_instructions failed: %s", e)
            return await self._run_dag(intent, t0, mem_ctx, decision.dag_instructions)
        if decision.action == "clarify":
            r = self._clarification_result(intent, t0, decision.clarification)
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r
        return await self._reply_direct(intent, t0, mem_ctx)

    async def run(self, intent: str) -> SupervisorResult:
        """Handle one user message via the single GOAT decision call.

        FLOW:
        1. init session → add user turn → memory turn (memory subsystem).
        2. pending-DAG fast path (start_dag tool).
        3. build context (GoatContext, ClarityContext, hints) — pure, no LLM.
        4. ONE GOAT decision call → {action, response, clarification, dag_instructions}.
        5. dispatch: direct → tool-enabled reply; clarify → question; dag → pipeline.
        """
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)
        if self._history is None:
            self._user_profile, self._history, self._behavior_style, _ = await init_session(
                self.memory_manager
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)

        # Pending DAG from a start_dag tool call — fire directly.
        pending_dag_session = await pop_pending_dag(self.memory_manager, self._session_id)
        if pending_dag_session:
            log.info("GOAT: pending DAG found session=%s — firing DAG", pending_dag_session)
            return await self._run_dag(intent, t0, mem_ctx, intent)

        # The ONE GOAT call decides everything from pure-built context.
        goat_ctx, clarity_ctx, hints = await self._build_context(intent, mem_ctx)
        from supervisor.pipeline.goat_decision import decide
        decision = await decide(self.registry, intent, goat_ctx, clarity_ctx, hints)
        depth = classify_intent(decision)
        log.info("GOAT decision: action=%s → %s intent=%.80s", decision.action, depth.value, intent)
        return await self._dispatch(intent, t0, mem_ctx, decision)

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
