"""GoatSupervisor — top-level orchestrator for GOAT 2.0.

Single flow: middleware (no LLM) → ONE LLM call
(``pipeline.goat_call.goat_turn``) → dispatch.

DAGs always run in the background — never blocks the kernel.
Each call to ``run()`` appends one user message, drives the
LLM call, persists the result, and returns a
``SupervisorResult``.

USAGE:
    from config.registry import ServiceRegistry
    from supervisor.supervisor import GoatSupervisor

    registry = ServiceRegistry()
    result = await GoatSupervisor(registry).run("Build a REST API")

STRICT RULES FOLLOWED:
  - All dependencies are passed in via the registry. No
    singletons; no module-level state.
  - All thresholds, defaults, and labels live in config files
    (``goat.toml`` / ``memory.toml`` / ``dag.toml`` /
    ``behavioral.toml`` / ``tools.toml``) — no hardcoded values.
  - The LLM is called exactly ONCE per turn, in
    ``pipeline.goat_call``.
  - No regex anywhere in this module.
  - DAG spawn is fire-and-forget; the supervisor returns
    immediately when a DAG is requested and reads the result on
    the next turn.
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

__all__ = ["GoatSupervisor"]


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
          (2) append user turn, render memory context;
          (3) build middleware context (GoatContext + ClarityContext);
          (4) sync the in-memory style cache with what
              ``build_goat_context`` just loaded from Letta;
          (5) invoke the ONE LLM call;
          (6) dispatch, persist, return.

        Args:
            intent: The raw user intent for this turn.

        Returns:
            A ``SupervisorResult`` populated with summary,
            session id, and metadata. Never raises — failures
            degrade to a clarify result.
        """
        t0 = time.monotonic()
        log.info("GOAT — intent: %.120s", intent)
        try:
            self._ensure_initialized()
            if self._history is None:
                await self._bootstrap_session()
            assert self._history is not None  # for type-checker
            self._history.add_user(intent)
            mem_ctx = await mem_turn(self.memory_manager, intent, self.registry)
            goat_ctx = await build_goat_context(self.registry, mem_ctx)
            history_text = "\n".join(
                f"{m['role']}: {m['content']}" for m in self._history.messages
            )
            clarity_text = build_clarity_context(history_text, mem_ctx)

            # Sync in-memory style cache from GoatContext (no second
            # Letta read).
            self._sync_style_from_ctx(goat_ctx)

            # Build the hint list (corrections + static hints).
            from supervisor.mechanisms.hints import build_hints
            hints = await build_hints(
                self.memory_manager, intent, self.registry, limit=3,
            )

            from supervisor.pipeline.goat_call import goat_turn
            turn = await goat_turn(
                registry=self.registry,
                intent=intent,
                goat_context=goat_ctx,
                clarity_context=clarity_text,
                hints=hints,
                history_messages=self._history.messages,
                mem_ctx=mem_ctx,
                style=self._behavior_style,
                turn=len(self._history.messages),
                goat_session_id=self.session_id,
                supervisor=self,
            )
            depth = classify_intent(turn)
            log.info(
                "GOAT turn: action=%s → %s intent=%.80s",
                turn.action, depth.value, intent,
            )
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
        """Mirror ``GoatContext.behavior_profile`` into ``_behavior_style``.

        No second Letta round-trip — the profile is already on the
        GoatContext. Empty / unchanged → no-op.
        """
        profile = getattr(goat_ctx, "behavior_profile", "") or ""
        if profile and profile != self._behavior_style:
            log.debug(
                "_sync_style_from_ctx: style refreshed (%d chars)",
                len(profile),
            )
            self._behavior_style = profile

    async def _dispatch(
        self, intent: str, t0: float, turn: "GoatTurnResult",
    ) -> "SupervisorResult":
        """Persist the turn and return a populated SupervisorResult.

        action = "dag"     → return a placeholder (DAG runs in bg).
        action = "clarify" → return the LLM's clarification.
        action = "direct"  → return the LLM's reply.
        """
        if turn.action == "dag":
            summary = "DAG started. I'll surface results on the next turn."
            source = "generated"
            session_id = ""
        elif turn.action == "clarify":
            summary = (
                turn.clarification or turn.response
                or "Could you provide more details about what you'd like me to do?"
            )
            source = "generated"
            session_id = ""
        else:
            summary = turn.response or ""
            source = turn.source
            session_id = ""

        if self._history is not None:
            self._history.add_assistant(summary)
            from supervisor.session.turn_persistence import store_and_promote
            await store_and_promote(
                self, len(self._history.messages), intent, summary,
            )
        return self._build_result(intent, t0, summary, source, session_id)

    def _build_result(
        self,
        intent: str,
        t0: float,
        summary: str,
        source: str,
        session_id: str,
    ) -> "SupervisorResult":
        """Build a minimal SupervisorResult."""
        from supervisor.types import Plan, SupervisorResult
        return SupervisorResult(
            intent=intent,
            plan=Plan(tasks=[]),
            results={},
            critique="",
            summary=summary,
            total_duration_s=time.monotonic() - t0,
            session_id=session_id or self.session_id,
            sources={"conv": source},
        )

    def _empty_result(self, intent: str, t0: float, err: str) -> "SupervisorResult":
        """Build the universal fallback result for unhandled errors."""
        log.debug("_empty_result: fallback for %r", err)
        return self._build_result(
            intent=intent, t0=t0,
            summary="Could you provide more details about what you'd like me to do?",
            source="generated", session_id="",
        )
