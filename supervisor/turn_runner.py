"""Turn runner — the per-turn orchestration logic for GoatSupervisor.

Extracted from ``supervisor.py`` to keep that file under the
260-line ceiling. The runner owns the per-turn lifecycle:

  1. Append user turn (buffered as pending — BUG-015).
  2. Build memory context, GoatContext, ClarityContext, hints.
  3. Invoke the single LLM call (capped by ``turn_timeout``).
  4. Dispatch → persist → return SupervisorResult.

On any failure path (timeout, exception), the pending user turn
is rolled back so the history never accumulates orphan user
messages.

USAGE:
    from supervisor.turn_runner import run_turn
    result = await run_turn(supervisor, intent)
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from supervisor.classification.classifier import classify_intent
from supervisor.classification.intent_clarity import build_clarity_context
from supervisor.pipeline.goat_enrichment import build_goat_context
from supervisor.session.mem_inject import mem_turn

if TYPE_CHECKING:
    from supervisor.pipeline.goat_call import GoatTurnResult
    from supervisor.supervisor import GoatSupervisor
    from supervisor.types import SupervisorResult

log = logging.getLogger("goat2.supervisor.turn_runner")

__all__ = ["run_turn"]


async def run_turn(supervisor: "GoatSupervisor", intent: str) -> "SupervisorResult":
    """Run one user turn end-to-end. Must always respond.

    Lifecycle:
      1. Bootstrap (subsystems + history) on first call.
      2. Enforce ``supervisor.max_turns`` (early return if exceeded).
      3. Buffer user turn as **pending** (BUG-015).
      4. Build memory context, GoatContext, ClarityContext, hints.
      5. Invoke the single LLM call (capped by ``turn_timeout``).
      6. Dispatch — persist turn + commit pending user — return.

    On failure:
      - Timeout or invoke error: rollback pending user, return timeout msg.
      - Any other exception: rollback pending user, return error msg.

    The pending user + rollback discipline ensures the conversation
    history never accumulates orphan user messages.
    """
    t0 = time.monotonic()
    log.info("GOAT — intent: %.120s", intent)
    try:
        supervisor._ensure_initialized()
        if supervisor._history is None:
            await supervisor._bootstrap_session()
        assert supervisor._history is not None

        settings = supervisor.registry.settings.supervisor
        max_turns = int(getattr(settings, "max_turns", 0) or 0)
        if max_turns and len(supervisor._history) >= max_turns:
            log.warning(
                "GoatSupervisor: max_turns=%d reached (history=%d) — refusing",
                max_turns, len(supervisor._history),
            )
            return supervisor._build_result(
                intent=intent, t0=t0,
                summary="Session turn limit reached. Start a new session to continue.",
                source="generated", session_id="",
            )

        # BUG-015: buffer the user turn as pending — only commits
        # after a successful assistant reply, so a failure path
        # cannot leave an orphan user message in history.
        supervisor._history.add_user(intent, pending=True)

        mem_ctx = await mem_turn(supervisor.memory_manager, intent)
        goat_ctx = await build_goat_context(supervisor.registry, mem_ctx)
        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in supervisor._history.messages
        )
        clarity_text = build_clarity_context(history_text, mem_ctx)
        supervisor._sync_style_from_ctx(goat_ctx)

        from supervisor.mechanisms.hints import build_hints
        hints = await build_hints(
            supervisor.memory_manager, intent, supervisor.registry, limit=3,
        )
        try:
            turn = await supervisor._invoke_turn(
                intent, goat_ctx, clarity_text, hints, mem_ctx,
            )
        except Exception as timeout_exc:
            # _TurnTimeoutError or any other invoke failure.
            log.warning("GoatSupervisor: turn failed — %s", timeout_exc)
            if supervisor._history is not None:
                supervisor._history.rollback_pending()
            return supervisor._build_result(
                intent=intent, t0=t0,
                summary="I took too long to respond. Please try a simpler request or start a new session.",
                source="generated", session_id="",
            )

        depth = classify_intent(turn)
        log.info(
            "GOAT turn: action=%s → %s intent=%.80s",
            turn.action, depth.value, intent,
        )
        return await supervisor._dispatch(intent, t0, turn)
    except Exception as exc:  # noqa: BLE001 — kernel must respond
        log.exception("GoatSupervisor.run: unhandled error: %s", exc)
        if supervisor._history is not None:
            supervisor._history.rollback_pending()
        return supervisor._empty_result(intent, t0, str(exc))
