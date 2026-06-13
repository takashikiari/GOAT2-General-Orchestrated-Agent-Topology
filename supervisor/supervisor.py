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
    from supervisor.pipeline.intent_clarity import ClarityResult

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
            # Behavioral learning: detect routing disagreement from user's next message
            # This is handled in the run() loop after user feedback, not here
            try:
                from supervisor.behavior.behavior_analyzer import analyze_style
                from supervisor.behavior.behavior_store import save_style
                from supervisor.behavior.behavior_session import get_recent_turns
                from supervisor.behavior.behavior_profile import serialize
                turns = await get_recent_turns(self.memory_manager, limit=10)
                if turns:
                    profile = await analyze_style(turns, self.registry)
                    if profile:
                        await save_style(self.memory_manager, serialize(profile))
            except Exception as e:
                log.debug("behavior analysis skipped: %s", e)
            asyncio.create_task(self._schedule_promotion(turn_count))
        except Exception as e:
            log.warning("Memory storage skipped: %s", e)

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

    async def _get_previous_routing(self) -> str | None:
        """Read the previous routing decision from working memory."""
        if not self.memory_manager:
            return None
        try:
            from config.roles import SESSION_ROLE as _SROLE
            key = f"goat:{self._session_id}:last_routing"
            record = await self.memory_manager.working.backend.get(_SROLE, key)
            if record is None:
                return None
            return record.get("content")
        except Exception as e:
            log.debug("_get_previous_routing failed: %s", e)
            return None

    async def _set_previous_routing(self, depth: IntentDepth) -> None:
        """Store the routing decision for the next turn to check against."""
        if not self.memory_manager:
            return
        try:
            from config.roles import SESSION_ROLE as _SROLE
            from config.limits import WORKING_MEMORY_TTL
            key = f"goat:{self._session_id}:last_routing"
            now = time.time()
            record = {
                "id": key,
                "agent_role": _SROLE,
                "key": key,
                "content": depth.value,
                "metadata": {"type": "routing_decision"},
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "created_at_ts": now,
                "expires_at": now + WORKING_MEMORY_TTL,
            }
            await self.memory_manager.working.backend.set(
                _SROLE, key, record, expires_at=record["expires_at"]
            )
        except Exception as e:
            log.debug("_set_previous_routing failed: %s", e)

    async def _clear_previous_routing(self) -> None:
        """Clear the previous routing decision."""
        if not self.memory_manager:
            return
        try:
            from config.roles import SESSION_ROLE as _SROLE
            key = f"goat:{self._session_id}:last_routing"
            await self.memory_manager.working.backend.delete(_SROLE, key)
        except Exception:
            pass

    async def _check_disagreement(
        self, user_message: str, previous_routing: str
    ) -> tuple[bool, str]:
        """Check if user message disagrees with previous routing decision."""
        from supervisor.pipeline.behavioral_learning import detect_routing_disagreement
        return await detect_routing_disagreement(user_message, previous_routing, self.registry)

    async def _store_routing_correction(
        self, intent: str, goat_routed: str, user_wanted: str
    ) -> None:
        """Store routing correction for behavioral learning."""
        from supervisor.pipeline.behavioral_learning import store_correction
        await store_correction(self.registry, intent, goat_routed, user_wanted)

    async def _run_dag(self, intent: str, t0: float, depth: IntentDepth, mem_ctx: str) -> SupervisorResult:
        """Thin wrapper over the extracted pipeline module."""
        from supervisor.pipeline.dag_execution import run_dag_pipeline
        return await run_dag_pipeline(self, intent, t0, depth, mem_ctx)

    async def _execute_with_depth(
        self, intent: str, t0: float, depth: IntentDepth, mem_ctx: str
    ) -> SupervisorResult:
        """Execute with a specific routing depth (after correction)."""
        await self._set_previous_routing(depth)
        if depth == IntentDepth.CONVERSATIONAL:
            r = await conv_result(
                intent, self._history.messages, self._user_profile or "",
                self._history.summary, mem_ctx, t0, self.registry, self._behavior_style,
                goat_session_id=self._session_id,
            )
            self._history.add_assistant(r.summary)
            await self._store_and_promote(len(self._history.messages), intent, r.summary)
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

        # Gate 1 — Intent clarity
        clarity = await self._check_intent_clarity(intent, mem_ctx)
        if not clarity.clear:
            log.info("GOAT: intent unclear — question=%.80s", clarity.clarification_question)
            r = self._clarification_result(intent, t0, clarity.clarification_question)
            self._history.add_assistant(r.summary)
            await self._store_and_promote(len(self._history.messages), intent, r.summary)
            return r

        # Gate 2 — DagPrompt validation
        dag_validity = await self._validate_dag_prompt(intent, mem_ctx)
        if not dag_validity.clear:
            log.info("GOAT: dag_prompt invalid — question=%.80s", dag_validity.clarification_question)
            r = self._clarification_result(intent, t0, dag_validity.clarification_question)
            self._history.add_assistant(r.summary)
            await self._store_and_promote(len(self._history.messages), intent, r.summary)
            return r

        return await self._run_dag(intent, t0, depth, mem_ctx)

    async def _check_intent_clarity(self, intent: str, mem_ctx: str) -> "ClarityResult":
        """Return ClarityResult from LLM intent clarity check.

        Returns ClarityResult(clear=True) on any failure — ambiguity never hard-blocks.
        """
        from supervisor.pipeline.intent_clarity import check_intent_clarity
        from supervisor.classification.classifier_prompt import format_history
        history_text = format_history(self._history.messages) if self._history else ""
        return await check_intent_clarity(intent, mem_ctx, history_text, self.registry)

    async def _validate_dag_prompt(self, intent: str, mem_ctx: str) -> "ClarityResult":
        """Build a DagPrompt and validate it for completeness and specificity.

        Returns ClarityResult(clear=False) with a specific clarification_question
        if the prompt is missing required information. Defaults to clear=True on
        any exception so validation never hard-blocks the pipeline.
        """
        from supervisor.pipeline.dag_prompt_builder import build_dag_prompt, validate_dag_prompt
        from supervisor.pipeline.intent_clarity import ClarityResult
        from supervisor.classification.classifier_prompt import format_history
        history_text = format_history(self._history.messages) if self._history else ""
        try:
            dag_prompt = await build_dag_prompt(intent, mem_ctx, history_text, self.registry)
            return await validate_dag_prompt(dag_prompt, intent, self.registry)
        except Exception as e:
            log.debug("_validate_dag_prompt failed — defaulting to clear: %s", e)
            return ClarityResult(clear=True, missing=[], clarification_question="")

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
        pending_dag_session = await self._pop_pending_dag()
        if pending_dag_session:
            log.info("GOAT: pending DAG found session=%s — firing DAG", pending_dag_session)
            return await self._run_dag(intent, t0, IntentDepth.COMPLEX, mem_ctx)

        # Check for routing disagreement with previous decision before classifying
        previous_routing = await self._get_previous_routing()
        if previous_routing:
            is_disagree, wanted = await self._check_disagreement(intent, previous_routing)
            if is_disagree:
                log.info("routing correction: was=%s wanted=%s", previous_routing, wanted)
                # Store the correction for future learning
                await self._store_routing_correction(intent, previous_routing, wanted)
                # Apply the correction: override the classification
                if wanted == "complex":
                    depth = IntentDepth.COMPLEX
                elif wanted == "conversational":
                    depth = IntentDepth.CONVERSATIONAL
                else:
                    depth = IntentDepth.ANALYTICAL
                # Clear previous routing and continue with the corrected depth
                await self._clear_previous_routing()
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
