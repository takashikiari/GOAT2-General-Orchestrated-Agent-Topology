"""GoatSupervisor — GOAT 2.0 top-level orchestrator. See docs/supervisor.md for full architecture."""
from __future__ import annotations
import uuid

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE
from supervisor.types import AgentRunner, Plan, SupervisorResult
from supervisor.session.history import ConversationHistory
from supervisor.identity import conv_result
from supervisor.classification.classifier import IntentDepth, classify_intent
from supervisor.session.mem_inject import mem_turn
from supervisor.session.session_init import init_session
from supervisor.behavior.behavior_session import finalize_behavior

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

# Capability summary written into DAG instructions so DAG knows what agents can do.
_DAG_CAPABILITIES_SUMMARY: str = (
    "tool_caller: file_read, file_write, file_create, file_list, file_search, file_grep, "
    "memory_recent, memory_get, memory_store, memory_search; "
    "researcher: web_search, memory_search; "
    "coder: file_read, file_write, file_create, shell(read-only); "
    "critic: memory_recent, memory_get(read-only); "
    "summarizer: memory_recent(read-only)"
)


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

    async def _store_and_promote(self, turn_count: int, intent: str, summary: str) -> None:
        """Store turn in working memory, auto-save to episodic tier, schedule promotion."""
        if not self.memory_manager:
            return
        try:
            from supervisor.session import store_turn, store_goat_turn
            await store_turn(self.memory_manager, turn_count, intent, summary)
            await store_goat_turn(self.memory_manager, self._session_id, intent, summary)
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

    async def _pop_pending_dag(self) -> str | None:
        """Read and delete goat:<session_id>:pending_dag from working memory."""
        if not self.memory_manager:
            return None
        try:
            from config.roles import SESSION_ROLE as _SROLE
            key = f"goat:{self._session_id}:pending_dag"
            record = await self.memory_manager.working.backend.get(_SROLE, key)
            if record is None:
                return None
            await self.memory_manager.working.backend.delete(_SROLE, key)
            return record.get("content")
        except Exception as e:
            log.debug("_pop_pending_dag failed: %s", e)
            return None

    async def _run_dag(self, intent: str, t0: float, depth: IntentDepth, mem_ctx: str) -> SupervisorResult:
        """Thin wrapper over the extracted pipeline module."""
        from supervisor.pipeline.dag_execution import run_dag_pipeline
        return await run_dag_pipeline(self, intent, t0, depth, mem_ctx)

    async def _check_intent_clarity(self, intent: str, mem_ctx: str) -> bool:
        """Return False when the LLM judges the intent too ambiguous for DAG execution."""
        from supervisor.pipeline.intent_clarity import check_intent_clarity
        from supervisor.classification.classifier_prompt import format_history
        history_text = format_history(self._history.messages) if self._history else ""
        return await check_intent_clarity(intent, mem_ctx, history_text, self.registry)

    async def run(self, intent: str) -> SupervisorResult:
        """Route intent: classify_intent decides conversational vs DAG.

        FLOW:
        1. Initialize session (profile, history, behavior style) on first call.
        2. Add user turn to history.
        3. Run memory turn (recall + fact extraction).
        4. Single LLM routing call via classify_intent: conversational or complex.
        5. CONVERSATIONAL → direct LLM reply via conv_result().
           After reply, check if GOAT called start_dag — if so, fire DAG as background task.
        6. COMPLEX → clarification gate → DAG pipeline.
        """
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)
        if self._history is None:
            self._user_profile, self._history, self._behavior_style, _ = await init_session(
                self.memory_manager
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)

        # Single classifier — classify_intent gathers active DAGs and context internally.
        depth = await classify_intent(
            intent, self.registry, self._history, session_id=self._session_id,
        )
        log.info("classify_intent: intent=%.80s → %s", intent, depth.value)

        if depth == IntentDepth.CONVERSATIONAL:
            r = await conv_result(
                intent, self._history.messages, self._user_profile or "",
                self._history.summary, mem_ctx, t0, self.registry, self._behavior_style,
                goat_session_id=self._session_id,
            )
            self._history.add_assistant(r.summary)
            await self._store_and_promote(len(self._history.messages), intent, r.summary)
            # If GOAT called start_dag during this turn, fire the DAG as a background task.
            pending_dag_id = await self._pop_pending_dag()
            if pending_dag_id:
                log.info("GOAT: pending DAG session=%s — firing background task", pending_dag_id)
                asyncio.create_task(self._run_dag(intent, t0, IntentDepth.COMPLEX, mem_ctx))
            return r

        # GOAT formulates and writes structured instructions for DAG before handing off.
        # DAG reads dag:<session_id>:instructions instead of raw intent.
        if self.memory_manager:
            try:
                from supervisor.session.session import write_dag_instructions
                await write_dag_instructions(
                    self.memory_manager, self._session_id,
                    intent, mem_ctx, _DAG_CAPABILITIES_SUMMARY,
                )
            except Exception as e:
                log.warning("write_dag_instructions failed (non-critical): %s", e)

        # Clarification gate: ask LLM if intent needs disambiguation before DAG dispatch.
        if not await self._check_intent_clarity(intent, mem_ctx):
            log.info("GOAT: intent unclear — returning clarification request")
            r = await conv_result(
                intent, self._history.messages, self._user_profile or "",
                self._history.summary, mem_ctx, t0, self.registry, self._behavior_style,
                goat_session_id=self._session_id,
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
