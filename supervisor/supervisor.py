"""GoatSupervisor — top-level orchestrator for GOAT 2.0.

Single flow: middleware (no LLM) → ONE LLM call
(``pipeline.goat_call.goat_turn``) → dispatch.

USAGE:
    from config.registry import ServiceRegistry
    from supervisor.supervisor import GoatSupervisor
    result = await GoatSupervisor(ServiceRegistry()).run("Build a REST API")

STRICT RULES:
  - All dependencies via the registry. No singletons.
  - All thresholds/defaults live in config files.
  - The LLM is called exactly ONCE per turn, in pipeline.goat_call.
  - No regex anywhere in this module.
  - DAG spawn is fire-and-forget; the supervisor returns immediately
    and reads the DAG result on the next turn.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from supervisor.classification.classifier import IntentDepth, classify_intent
from supervisor.classification.intent_clarity import build_clarity_context
from supervisor.pipeline.goat_enrichment import build_goat_context
from supervisor.session.history import ConversationHistory
from supervisor.session.mem_inject import mem_turn

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from supervisor.pipeline.goat_call import GoatTurnResult
    from supervisor.types import SupervisorResult

log = logging.getLogger("goat2.supervisor")

__all__ = ["GoatSupervisor", "_TurnTimeoutError"]


class _TurnTimeoutError(Exception):
    """Raised when a turn exceeds ``supervisor.turn_timeout`` seconds.

    Caught by ``GoatSupervisor.run`` and converted into a
    clarify-style fallback reply — the kernel must always respond.
    """

    def __init__(self, timeout_s: int) -> None:
        super().__init__(f"turn exceeded {timeout_s}s")
        self.timeout_s = timeout_s


class GoatSupervisor:
    """GOAT 2.0 orchestrator — single-call LLM, middleware-only flow.

    Attributes:
        registry: ServiceRegistry (settings, memory, tools).
        memory_manager: Shortcut to ``registry.memory_manager``.
        session_id: UUID for this supervisor instance.
        _behavior_style: Cached raw style profile text from Letta.
        _history: Per-session ``ConversationHistory`` (lazy).
        _initialized: True after first ``run()`` (subsystems booted).
    """

    __slots__ = (
        "registry",
        "memory_manager",
        "session_id",
        "_behavior_style",
        "_history",
        "_initialized",
        "_semaphore",
        "_active_dag_tasks",
    )

    def __init__(self, registry: "ServiceRegistry") -> None:
        self.registry = registry
        self.memory_manager = registry.memory_manager
        self.session_id = str(uuid.uuid4())
        self._behavior_style: str = ""
        self._history: ConversationHistory | None = None
        self._initialized: bool = False
        self._semaphore = asyncio.Semaphore(
            int(getattr(registry.settings.supervisor, "max_workers", 1) or 1)
        )
        self._active_dag_tasks: dict[str, asyncio.Task] = {}
        log.info("GoatSupervisor: ready (session=%s)", self.session_id)

    # ── Public API ──

    async def run(self, intent: str) -> "SupervisorResult":
        """Handle one user message. Must always respond.

        Steps (defensive — never raises):
          (1) ensure subsystems are booted;
          (2) gate on supervisor.max_turns;
          (3) append user turn, render memory context;
          (4) build middleware context (GoatContext + ClarityContext);
          (5) sync the in-memory style cache from GoatContext;
          (6) invoke the ONE LLM call (capped by supervisor.turn_timeout);
          (7) dispatch, persist, return.
        """
        t0 = time.monotonic()
        log.info("GOAT — intent: %.120s", intent)
        try:
            self._ensure_initialized()
            if self._history is None:
                await self._bootstrap_session()
            assert self._history is not None
            settings = self.registry.settings.supervisor
            max_turns = int(getattr(settings, "max_turns", 0) or 0)
            if max_turns and len(self._history) >= max_turns:
                log.warning("GoatSupervisor: max_turns=%d reached (history=%d) — refusing",
                            max_turns, len(self._history))
                return self._build_result(
                    intent=intent, t0=t0,
                    summary="Session turn limit reached. Start a new session to continue.",
                    source="generated", session_id="",
                )
            self._history.add_user(intent)
            mem_ctx = await mem_turn(self.memory_manager, intent)
            goat_ctx = await build_goat_context(self.registry, mem_ctx)
            history_text = "\n".join(
                f"{m['role']}: {m['content']}" for m in self._history.messages
            )
            clarity_text = build_clarity_context(history_text, mem_ctx)
            self._sync_style_from_ctx(goat_ctx)

            from supervisor.mechanisms.hints import build_hints
            hints = await build_hints(
                self.memory_manager, intent, self.registry, limit=3,
            )
            try:
                turn = await self._invoke_turn(
                    intent, goat_ctx, clarity_text, hints, mem_ctx,
                )
            except _TurnTimeoutError:
                log.warning("GoatSupervisor: turn timeout — failing fast")
                return self._build_result(
                    intent=intent, t0=t0,
                    summary="I took too long to respond. Please try a simpler request or start a new session.",
                    source="generated", session_id="",
                )
            depth = classify_intent(turn)
            log.info("GOAT turn: action=%s → %s intent=%.80s", turn.action, depth.value, intent)
            return await self._dispatch(intent, t0, turn)
        except Exception as exc:  # noqa: BLE001 — kernel must respond
            log.exception("GoatSupervisor.run: unhandled error: %s", exc)
            return self._empty_result(intent, t0, str(exc))

    # ── Internal helpers ──

    def _ensure_initialized(self) -> None:
        """Lazy-init subsystems on the first ``run()`` call. Idempotent."""
        if self._initialized:
            return
        self._initialized = True
        log.debug("GoatSupervisor: subsystems booted")

    async def _invoke_turn(
        self, intent: str, goat_ctx, clarity_text, hints, mem_ctx: str,
    ) -> "GoatTurnResult":
        """Invoke the single LLM call, optionally bounded by turn_timeout.

        Raises:
            _TurnTimeoutError: when asyncio.wait_for fires.
        """
        from supervisor.pipeline.goat_call import goat_turn
        turn_timeout = int(
            getattr(self.registry.settings.supervisor, "turn_timeout", 0) or 0
        )
        kwargs = dict(
            registry=self.registry, intent=intent, goat_context=goat_ctx,
            clarity_context=clarity_text, hints=hints,
            history_messages=self._history.messages if self._history else [],
            mem_ctx=mem_ctx, style=self._behavior_style,
            turn=len(self._history.messages) if self._history else 0,
            goat_session_id=self.session_id, supervisor=self,
        )
        if turn_timeout > 0:
            try:
                return await asyncio.wait_for(goat_turn(**kwargs), timeout=turn_timeout)
            except asyncio.TimeoutError as exc:
                raise _TurnTimeoutError(turn_timeout) from exc
        return await goat_turn(**kwargs)

    async def _bootstrap_session(self) -> None:
        """Lazy session init — pull history + style from memory."""
        self._history = ConversationHistory()
        from supervisor.behavior.store import load_style
        try:
            self._behavior_style = await load_style(self.memory_manager) or ""
        except Exception as exc:  # noqa: BLE001
            log.debug("_bootstrap_session: style load failed: %s", exc)
            self._behavior_style = ""

    def _sync_style_from_ctx(self, goat_ctx) -> None:
        """Mirror ``GoatContext.behavior_profile`` into ``_behavior_style``."""
        profile = getattr(goat_ctx, "behavior_profile", "") or ""
        if profile and profile != self._behavior_style:
            log.debug("_sync_style_from_ctx: style refreshed (%d chars)", len(profile))
            self._behavior_style = profile

    async def _dispatch(
        self, intent: str, t0: float, turn: "GoatTurnResult",
    ) -> "SupervisorResult":
        """Persist the turn and return a populated SupervisorResult.

        action = "dag"     → placeholder (DAG runs in bg).
        action = "clarify" → return the LLM's clarification.
        action = "direct"  → return the LLM's reply.
        """
        action = turn.action
        if action == "dag":
            summary = "DAG started. I'll surface results on the next turn."
            source = "generated"
            session_id = ""
        elif action == "clarify":
            summary = (turn.clarification or turn.response
                       or "Could you provide more details about what you'd like me to do?")
            source = "generated"
            session_id = ""
        else:
            summary = turn.response or ""
            source = turn.source
            session_id = ""

        if self._history is not None:
            self._history.add_assistant(summary)
            from supervisor.session.turn_persistence import store_and_promote
            await store_and_promote(self, len(self._history.messages), intent, summary)
        return self._build_result(intent, t0, summary, source, session_id, action=action)

    def _build_result(
        self, intent: str, t0: float, summary: str, source: str, session_id: str,
        action: str = "direct",
    ) -> "SupervisorResult":
        """Build a minimal SupervisorResult."""
        from supervisor.types import SupervisorResult
        return SupervisorResult(
            intent=intent, summary=summary,
            session_id=session_id or self.session_id,
            sources={"conv": source},
            duration_s=time.monotonic() - t0,
            action=action,
        )

    def _empty_result(self, intent: str, t0: float, err: str) -> "SupervisorResult":
        """Build the universal fallback result for unhandled errors.

        Thin wrapper over ``supervisor.errors_fallback.empty_error_result``
        — that module owns the formatting policy (template, truncation,
        include-type). Kept as a method on ``GoatSupervisor`` so
        existing call sites in ``run`` don't need to change.
        """
        from supervisor.errors_fallback import empty_error_result
        return empty_error_result(self, intent=intent, t0=t0, err=err)  # type: ignore[arg-type]
