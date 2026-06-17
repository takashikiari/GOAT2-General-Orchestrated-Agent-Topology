"""GoatSupervisor — GOAT 2.0 top-level orchestrator. See docs/supervisor.md.

ONE GOAT LLM call per turn (``goat_call.goat_turn``) combines routing AND
tool-enabled response. Middleware only builds context (no LLM). The action
(``direct`` / ``clarify`` / ``dag``) is inferred from the tool-call trace
(``start_dag`` → dag) and a ``[CLARIFY]`` marker in the response.

GOAT is the kernel — always responsive, never blocks. DAGs spawned via
``spawn_dag_background`` are detached background tasks; they write
status/result to working memory, GOAT reads on the next turn.
"""
from __future__ import annotations
import re
import uuid

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from supervisor.types import AgentRunner, Plan, SupervisorResult
from supervisor.session.history import ConversationHistory
from supervisor.classification.classifier import IntentDepth, classify_intent
from supervisor.session.mem_inject import mem_turn
from supervisor.session.session_init import init_session
from supervisor.behavior.behavior_session import finalize_behavior
from supervisor.session.turn_persistence import store_and_promote
from supervisor.session.routing_state import pop_pending_dag
from tools.dag import background as dag_background

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import ServiceRegistry
    from agents.critique import CriticVerdict
    from supervisor.pipeline.goat_call import GoatTurnResult

log = logging.getLogger("goat2.supervisor")

__all__ = ["GoatSupervisor"]

_FALLBACK_CLARIFICATION = "Could you provide more details about what you'd like me to do?"

# Hard upper bound on the time GOAT waits for collect_finished(); purely defensive.
_COLLECT_FINISHED_TIMEOUT_S: float = 1.0


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
        self._active_dag_tasks: dict[str, asyncio.Task] = {}
        self._initialized: bool = False
        self._memory_daemon = None
        self._turn_counter: int = 0
        self._tools_watcher = None

    def spawn_dag_background(self, dag_instructions: str, session_id: str) -> "asyncio.Task":
        """Spawn the DAG as a detached background task — GOAT returns immediately."""
        from tools.dag import background as dag_background
        return dag_background.spawn(self, dag_instructions, session_id)

    @staticmethod
    def _strip_dsml(text: str) -> str:
        """Remove DeepSeek DSML markers from text."""
        text = re.sub(r'</?｜｜DSML｜｜[^>]*>', '', text)
        text = re.sub(r'/DSML[A-Za-z_/]*', '', text)
        return text.strip()

    def _schedule_working_memory_flush(self) -> None:
        """Fire-and-forget working-memory flush at session start."""
        from supervisor.session_init_flush import schedule_working_memory_flush
        schedule_working_memory_flush(self.registry)

    def _start_tools_watcher(self) -> None:
        """Start the hot-reload watcher for every tools package.

        Monitors every Python package under ``tools/`` plus the external
        ``dynamic_tools/`` root. Idempotent — only creates a new watcher
        the first time. ``start()`` is itself a no-op when no categories
        are discoverable.
        """
        from tools.hot_reload import ToolsWatcher
        if self._tools_watcher is None:
            self._tools_watcher = ToolsWatcher()
        asyncio.create_task(
            self._tools_watcher.start(self.registry),
            name="tools_watcher_start",
        )

    async def get_dag_status(self, session_id: str) -> dict:
        """Report a background DAG's status from its task state + working memory."""
        from tools.dag import background as dag_background
        return await dag_background.status(self, session_id)

    def _dag_started_result(self, intent: str, t0: float, session_id: str) -> SupervisorResult:
        """Immediate result returned the moment a background DAG is spawned."""
        return SupervisorResult(
            intent=intent, plan=Plan(tasks=[]), results={}, critique="",
            summary="DAG started, monitoring in background...",
            sources={"conv": "generated"}, session_id=session_id,
            total_duration_s=time.monotonic() - t0,
        )

    def _clarification_result(self, intent: str, t0: float, question: str) -> SupervisorResult:
        """Result that surfaces a clarification question to the user."""
        return SupervisorResult(
            intent=intent, plan=Plan(tasks=[]), results={}, critique="",
            summary=question or _FALLBACK_CLARIFICATION,
            sources={"conv": "generated"}, total_duration_s=time.monotonic() - t0,
        )

    async def _build_context(self, intent: str, mem_ctx: str):
        """Build decision context — pure, NO LLM. Returns (goat_ctx, clarity_ctx, hints)."""
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
        self, intent: str, t0: float, mem_ctx: str, turn: "GoatTurnResult", goat_ctx=None,
    ) -> SupervisorResult:
        """Execute the single-call turn's action.

        ``dag`` → "DAG started, monitoring in background…" (the LLM
        already spawned the DAG via start_dag). ``clarify`` → the LLM's
        question. ``direct`` → the LLM's reply (with tool use already
        accounted for in the single call). All three strip DSML markers
        and persist the turn.
        """
        if turn.action == "dag":
            r = self._dag_started_result(intent, t0, "")
        elif turn.action == "clarify":
            r = self._clarification_result(
                intent, t0, turn.clarification or turn.response or _FALLBACK_CLARIFICATION,
            )
        else:
            r = SupervisorResult(
                intent=intent, plan=Plan(tasks=[]), results={}, critique="",
                summary=turn.response, sources={"conv": turn.source},
                total_duration_s=time.monotonic() - t0,
            )
        r.summary = self._strip_dsml(r.summary)
        self._history.add_assistant(r.summary)
        await store_and_promote(self, len(self._history.messages), intent, r.summary)
        return r

    async def run(self, intent: str) -> SupervisorResult:
        """Handle one user message. GOAT is the kernel — must respond on every turn."""
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)

        if not self._initialized:
            self._initialized = True
            self._schedule_working_memory_flush()
            from supervisor.session.memory_housekeeping import start_memory_daemon
            start_memory_daemon(self)
            self._start_tools_watcher()
        if self._history is None:
            self._user_profile, self._history, self._behavior_style, _ = await init_session(
                self.memory_manager
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)

        # Surface finished background DAGs (non-blocking by design).
        try:
            dag_update = await asyncio.wait_for(
                dag_background.collect_finished(self), timeout=_COLLECT_FINISHED_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.warning("GOAT: collect_finished timed out")
            dag_update = ""
        if dag_update:
            mem_ctx = f"{dag_update}\n{mem_ctx}" if mem_ctx else dag_update

        # Pending DAG from a start_dag tool call — spawn detached.
        pending_dag_session = await pop_pending_dag(self.memory_manager, self._session_id)
        if pending_dag_session:
            log.info("GOAT: pending DAG session=%s — spawning background", pending_dag_session)
            self.spawn_dag_background(intent, pending_dag_session)
            r = self._dag_started_result(intent, t0, pending_dag_session)
            r.summary = self._strip_dsml(r.summary)
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r

        # Build pure middleware context — no LLM.
        goat_ctx, clarity_ctx, hints = await self._build_context(intent, mem_ctx)

        # ONE GOAT call decides AND responds in the same pass.
        from supervisor.pipeline.goat_call import goat_turn
        turn = await goat_turn(
            self.registry, intent, goat_ctx, clarity_ctx, hints,
            self._history.messages, mem_ctx,
            profile=self._user_profile or "",
            summary=self._history.summary,
            style=self._behavior_style,
            turn=len(self._history.messages),
            onboarding_done=await self._onboarding_done(),
            goat_session_id=self._session_id,
            supervisor=self,
        )
        depth = classify_intent(turn)
        log.info(
            "GOAT turn: action=%s → %s intent=%.80s",
            turn.action, depth.value, intent,
        )
        result = await self._dispatch(intent, t0, mem_ctx, turn, goat_ctx)

        from supervisor.session.memory_housekeeping import tick_gc
        tick_gc(self)

        return result

    async def _onboarding_done(self) -> bool:
        """Read the onboarding flag from working memory; default True when missing."""
        from supervisor.identity import check_onboarding_done
        return await check_onboarding_done(self.memory_manager)

    async def finalize_session(self) -> None:
        """Analyze session turns and persist updated behavior profile to Letta."""
        self._behavior_style = await finalize_behavior(
            self.memory_manager, self._history, self._behavior_style, self.registry
        )
        from supervisor.session.memory_housekeeping import finalize_memory
        await finalize_memory(self)
        # Stop the dynamic-tools watcher. Safe when never started.
        if self._tools_watcher is not None:
            try:
                await self._tools_watcher.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("finalize_session: tools_watcher.stop failed: %s", exc)

    def register_agent(self, role: str, runner: AgentRunner) -> None:
        """Register a pre-built async runner under ``role``."""
        self.agent_registry.register(role, runner)

    def make_agent(self, role: str, model_key: str, system_prompt: str) -> AgentRunner:
        """Build and register a simple LLM runner from a model key + system prompt."""
        return self.agent_registry.make_and_register(role, model_key, system_prompt)
