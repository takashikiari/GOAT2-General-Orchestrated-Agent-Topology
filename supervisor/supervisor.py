"""GoatSupervisor — GOAT 2.0 top-level orchestrator. See docs/supervisor.md for full architecture.

Single-call architecture: one GOAT decision LLM call (``goat_decision.decide``)
replaces the former 6-call routing pipeline. Middleware only builds context
(no LLM). Based on the decision's action the supervisor either replies
directly (tool-enabled), asks a clarification, or runs the DAG.

GOAT is the kernel — always responsive, never blocks. DAGs spawned via
``spawn_dag_background`` are detached background tasks; they write
status/result to working memory, GOAT reads on the next turn.
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
from supervisor.pipeline import dag_background

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
        # Detached DAGs (session_id → Task); set True after the first run() flush.
        self._active_dag_tasks: dict[str, asyncio.Task] = {}
        self._initialized: bool = False
        # Set by supervisor.session.memory_housekeeping on first run().
        self._memory_daemon = None
        # Per-turn GC cadence (incremented in run()).
        self._turn_counter: int = 0
        # Hot-reload watcher for dynamic tools — started in run().
        self._tools_watcher = None

    def spawn_dag_background(self, dag_instructions: str, session_id: str) -> "asyncio.Task":
        """Spawn the DAG as a detached background task — GOAT returns immediately."""
        from supervisor.pipeline import dag_background
        return dag_background.spawn(self, dag_instructions, session_id)

    def _schedule_working_memory_flush(self) -> None:
        """Fire-and-forget working-memory flush at session start."""
        from supervisor.session_init_flush import schedule_working_memory_flush
        schedule_working_memory_flush(self.registry)

    def _start_tools_watcher(self) -> None:
        """Start the hot-reload watcher for user-defined dynamic tools.

        Idempotent — only creates a new watcher the first time. ``start()``
        is itself a no-op when the resolved watch directory does not
        exist (production deployments can leave hot-reload disabled).
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
        from supervisor.pipeline import dag_background
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

    async def _reply_direct(self, intent: str, t0: float, mem_ctx: str) -> SupervisorResult:
        """Generate a tool-enabled conversational reply (memory/web available)."""
        r = await conv_result(
            intent, self._history.messages, self._user_profile or "",
            self._history.summary, mem_ctx, t0, self.registry, self._behavior_style,
            goat_session_id=self._session_id, supervisor=self,
        )
        self._history.add_assistant(r.summary)
        await store_and_promote(self, len(self._history.messages), intent, r.summary)
        # Pending DAG from a start_dag tool call — spawn detached.
        pending = await pop_pending_dag(self.memory_manager, self._session_id)
        if pending:
            log.info("GOAT: pending DAG session=%s — spawning background", pending)
            self.spawn_dag_background(intent, pending)
        return r

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
        self, intent: str, t0: float, mem_ctx: str, decision: "GoatDecision", goat_ctx=None,
    ) -> SupervisorResult:
        """Execute GOAT's decision. dag → spawn background; clarify → question; direct → reply."""
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
            session_id = str(uuid.uuid4())
            dag_instr = decision.dag_instructions or intent
            if goat_ctx.workspace and goat_ctx.workspace not in dag_instr:
                dag_instr = f"Workspace root: {goat_ctx.workspace}\n\n" + dag_instr
            # Spawn the DAG detached; GOAT never blocks.
            self.spawn_dag_background(dag_instr, session_id)
            r = self._dag_started_result(intent, t0, session_id)
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r
        if decision.action == "clarify":
            r = self._clarification_result(intent, t0, decision.clarification)
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r
        return await self._reply_direct(intent, t0, mem_ctx)

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
            self._history.add_assistant(r.summary)
            await store_and_promote(self, len(self._history.messages), intent, r.summary)
            return r

        # ONE GOAT call decides everything from pure-built context.
        goat_ctx, clarity_ctx, hints = await self._build_context(intent, mem_ctx)
        from supervisor.pipeline.goat_decision import decide
        decision = await decide(self.registry, intent, goat_ctx, clarity_ctx, hints)
        depth = classify_intent(decision)
        log.info("GOAT decision: action=%s → %s intent=%.80s", decision.action, depth.value, intent)
        result = await self._dispatch(intent, t0, mem_ctx, decision, goat_ctx)

        from supervisor.session.memory_housekeeping import tick_gc
        tick_gc(self)

        return result

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

    # ── DAG control surface — thin pass-throughs to dag_control_methods. ──
    async def pause_dag(self, session_id: str) -> None:
        """Pause a running DAG after its current wave."""
        from supervisor.dag_control_methods import pause_dag as _f
        await _f(self, session_id)

    async def resume_dag(self, session_id: str) -> None:
        """Resume a paused DAG."""
        from supervisor.dag_control_methods import resume_dag as _f
        await _f(self, session_id)

    async def stop_dag(self, session_id: str) -> None:
        """Stop a running DAG after its current wave."""
        from supervisor.dag_control_methods import stop_dag as _f
        await _f(self, session_id)

    async def get_dag_updates(self, session_id: str) -> dict | None:
        """Read dag:<session_id>:progress; returns the progress dict or None."""
        from supervisor.dag_control_methods import get_dag_updates as _f
        return await _f(self, session_id)
