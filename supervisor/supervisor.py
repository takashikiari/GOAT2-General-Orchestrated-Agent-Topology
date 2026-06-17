"""GoatSupervisor — GOAT 2.0 top-level orchestrator. See docs/supervisor.md.

ONE GOAT LLM call per turn (``goat_call.goat_turn``) combines routing AND
tool-enabled response. Middleware only builds context (no LLM). The action
(``direct`` / ``clarify`` / ``dag``) is inferred from the tool-call trace
(``start_dag`` → dag) and a ``[CLARIFY]`` marker in the response.

GOAT is the kernel — always responsive, never blocks. DAGs spawned via
``spawn_dag_background`` are detached background tasks; they write
status/result to working memory, GOAT reads on the next turn.

DSML stripping is intentionally NOT done here. ``tool_runner._call_with_tools``
strips DeepSeek markers from the LLM's response, and
``ConversationHistory.add_assistant`` strips them again before the next
prompt. Keeping a third stripper at the supervisor would just be a
duplicated regex masquerading as a safety net.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from supervisor.behavior.behavior_session import finalize_behavior
from supervisor.classification.classifier import classify_intent
from supervisor.session.history import ConversationHistory
from supervisor.session.mem_inject import mem_turn
from supervisor.session.routing_state import pop_pending_dag
from supervisor.session.session_init import init_session
from supervisor.session.turn_persistence import store_and_promote
from supervisor.types import Plan, SupervisorResult
from tools.dag import background as dag_background

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from memory.shared import MemoryManager
    from supervisor.pipeline.goat_call import GoatTurnResult

log = logging.getLogger("goat2.supervisor")

__all__ = ["GoatSupervisor"]

# Only hardcoded strings in this file: a single clarification fallback and
# the immediate "DAG started" string shown to the user.
_FALLBACK_CLARIFICATION = "Could you provide more details about what you'd like me to do?"
_DAG_STARTED_SUMMARY = "DAG started. I'll surface results on the next turn."

# Hard upper bound on the time GOAT waits for collect_finished(); purely defensive.
_COLLECT_FINISHED_TIMEOUT_S: float = 1.0


def _empty_plan_result(
    intent: str, t0: float, summary: str, source: str, session_id: str = "",
) -> SupervisorResult:
    """Build a minimal SupervisorResult with an empty plan/results — shared by the 3 dispatch actions."""
    return SupervisorResult(
        intent=intent, plan=Plan(tasks=[]), results={}, critique="",
        summary=summary, sources={"conv": source}, session_id=session_id,
        total_duration_s=time.monotonic() - t0,
    )


class GoatSupervisor:
    """GOAT 2.0 orchestrator — session, tiered memory, single-call routing, DAG execution."""

    def __init__(self, registry: "ServiceRegistry") -> None:
        self.registry = registry
        self.memory_manager = registry.memory_manager
        self._settings = registry.settings
        self._semaphore = asyncio.Semaphore(self._settings.supervisor.max_workers)
        self._history: ConversationHistory | None = None
        self._user_profile: str | None = None
        self._behavior_style: str = ""
        self._session_id: str = str(uuid.uuid4())
        self._active_dag_tasks: dict[str, asyncio.Task] = {}
        self._initialized: bool = False
        self._memory_daemon = None
        self._turn_counter: int = 0
        self._tools_watcher = None
        log.info("GoatSupervisor: ready (session=%s)", self._session_id)

    # ── Public API ──────────────────────────────────────────────────────

    def spawn_dag_background(self, dag_instructions: str, session_id: str) -> "asyncio.Task":
        """Detach a DAG as a background asyncio.Task; GOAT returns immediately."""
        return dag_background.spawn(self, dag_instructions, session_id)

    async def finalize_session(self) -> None:
        """End-of-session: persist behavior profile, then close the memory daemon."""
        self._behavior_style = await finalize_behavior(
            self.memory_manager, self._history, self._behavior_style, self.registry,
        )
        from supervisor.session.memory_housekeeping import finalize_memory
        await finalize_memory(self)
        if self._tools_watcher is not None:
            try:
                await self._tools_watcher.stop()
            except Exception as exc:  # noqa: BLE001 — best-effort shutdown
                log.debug("finalize_session: tools_watcher.stop failed: %s", exc)

    # ── Main turn entry point ───────────────────────────────────────────

    async def run(self, intent: str) -> SupervisorResult:
        """Handle one user message. GOAT is the kernel — must respond on every turn.

        Steps (sequential, all defensive — never raises):
            1. Lazy session init: memory daemon + tools watcher + history/profile.
            2. Append the user turn; pull working-memory context.
            3. Surface any finished background DAGs (bounded wait).
            4. If a DAG was requested by the LLM last turn, spawn it detached and return.
            5. Build pure middleware context; call the single GOAT LLM turn.
            6. Dispatch based on action; tick the per-turn GC.
        """
        t0 = time.monotonic()
        log.info("GOAT 2.0 — intent: %.120s", intent)
        self._ensure_initialized()
        if self._history is None:
            self._user_profile, self._history, self._behavior_style, _ = await init_session(
                self.memory_manager,
            )
        self._history.add_user(intent)
        mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)

        # 3. Surface finished background DAGs (non-blocking by design).
        dag_update = await self._collect_finished_dag_update()
        if dag_update:
            mem_ctx = f"{dag_update}\n{mem_ctx}" if mem_ctx else dag_update

        # 4. Pending DAG from a start_dag tool call — spawn detached, return immediately.
        pending_dag_session = await pop_pending_dag(self.memory_manager, self._session_id)
        if pending_dag_session:
            log.info("GOAT: pending DAG session=%s — spawning background", pending_dag_session)
            self.spawn_dag_background(intent, pending_dag_session)
            return await self._record_turn(intent, t0, _DAG_STARTED_SUMMARY, "generated", pending_dag_session)

        # 5. Build pure middleware context; the one GOAT call decides AND responds.
        goat_ctx, clarity_ctx, hints = await self._build_context(intent, mem_ctx)
        turn = await self._goat_turn(intent, goat_ctx, clarity_ctx, hints, mem_ctx)
        depth = classify_intent(turn)
        log.info("GOAT turn: action=%s → %s intent=%.80s", turn.action, depth.value, intent)

        # 6. Dispatch + per-turn GC.
        result = await self._dispatch(intent, t0, turn)
        from supervisor.session.memory_housekeeping import tick_gc
        tick_gc(self)
        return result

    # ── Internal helpers ────────────────────────────────────────────────

    def _ensure_initialized(self) -> None:
        """Lazy-init subsystems on the supervisor's first run() call. Idempotent."""
        if self._initialized:
            return
        self._initialized = True
        from supervisor.session.memory_housekeeping import start_memory_daemon
        from supervisor.session_init_flush import schedule_working_memory_flush
        schedule_working_memory_flush(self.registry)
        start_memory_daemon(self)
        self._start_tools_watcher()

    def _start_tools_watcher(self) -> None:
        """Start the hot-reload watcher for every tools package (idempotent)."""
        from tools.hot_reload import ToolsWatcher
        if self._tools_watcher is None:
            self._tools_watcher = ToolsWatcher()
        asyncio.create_task(
            self._tools_watcher.start(self.registry),
            name="tools_watcher_start",
        )

    async def _collect_finished_dag_update(self) -> str:
        """Read any finished background DAGs; bounded to ``_COLLECT_FINISHED_TIMEOUT_S``."""
        try:
            return await asyncio.wait_for(
                dag_background.collect_finished(self),
                timeout=_COLLECT_FINISHED_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.warning("GOAT: collect_finished timed out")
            return ""
        except Exception as exc:  # noqa: BLE001
            log.debug("GOAT: collect_finished failed: %s", exc)
            return ""

    async def _goat_turn(self, intent, goat_ctx, clarity_ctx, hints, mem_ctx) -> "GoatTurnResult":
        """The single GOAT LLM call. Delegates to ``goat_call.goat_turn``."""
        from supervisor.pipeline.goat_call import goat_turn
        return await goat_turn(
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

    async def _onboarding_done(self) -> bool:
        """Read the onboarding flag from working memory; default True when missing."""
        from supervisor.identity import check_onboarding_done
        return await check_onboarding_done(self.memory_manager)

    async def _build_context(self, intent: str, mem_ctx: str):
        """Build the pure middleware context (no LLM) for one turn."""
        from supervisor.classification.classifier_prompt import format_dialogue
        from supervisor.pipeline.behavioral_learning import recall_corrections
        from supervisor.pipeline.goat_enrichment import build_goat_context
        from supervisor.pipeline.intent_clarity import build_clarity_context
        goat_ctx = build_goat_context(self.registry, mem_ctx)
        history_text = format_dialogue(self._history.messages) if self._history else ""
        clarity_ctx = build_clarity_context(history_text, mem_ctx)
        hints = await recall_corrections(self.registry, limit=3)
        return goat_ctx, clarity_ctx, hints

    async def _record_turn(
        self, intent: str, t0: float, summary: str, source: str, session_id: str = "",
    ) -> SupervisorResult:
        """Build an empty-plan SupervisorResult and persist it to history + memory."""
        result = _empty_plan_result(intent, t0, summary, source, session_id)
        self._history.add_assistant(result.summary)
        await store_and_promote(self, len(self._history.messages), intent, result.summary)
        return result

    async def _dispatch(self, intent: str, t0: float, turn: "GoatTurnResult") -> SupervisorResult:
        """Build the SupervisorResult for the action the LLM chose this turn.

        action = "dag"      → "DAG started" result (supervisor returns immediately).
        action = "clarify"  → the LLM's clarification question (or fallback).
        action = "direct"   → the LLM's reply text.
        All three paths then persist the turn to history + memory before returning.
        """
        if turn.action == "dag":
            summary, source, session_id = _DAG_STARTED_SUMMARY, "generated", ""
        elif turn.action == "clarify":
            summary = turn.clarification or turn.response or _FALLBACK_CLARIFICATION
            source, session_id = "generated", ""
        else:
            summary, source, session_id = turn.response, turn.source, ""
        return await self._record_turn(intent, t0, summary, source, session_id)
