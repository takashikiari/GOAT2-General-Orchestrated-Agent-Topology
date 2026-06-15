"""Per-task retry wrapper for DAG workflow.

Wraps a runner with up to ``MAX_RETRIES`` retries on failure. Each retry
appends a failure-context block to the task's prompt so the LLM knows
what went wrong last time and can adapt its strategy. After the retry
budget is exhausted, the wrapper synthesises an error string in the
``TASK_ERROR:`` format that the rest of the pipeline can detect.

This is intentionally a separate module — ``workflow.py`` is already at
the 260-line ceiling, and the retry logic has enough state and edge
cases (prompt mutation, source reset, hash recomputation) to live on
its own.

GOAT-LEVEL SAFETY:
    The wrapper is bounded by ``MAX_RETRIES`` so a misbehaving runner
    cannot spin forever. Each retry uses a fresh ``task.prompt`` copy so
    subsequent tasks in the DAG see the original prompt on subsequent
    executions (the mutation is local to the retry call).
"""
from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.types import AgentTask
    from config.agent_types import AgentResult

log = logging.getLogger("goat2.supervisor.pipeline.task_retry")

__all__ = ["MAX_RETRIES", "run_with_retry", "format_task_error", "is_task_error"]

# Maximum number of retries after the initial attempt. Total attempts
# per task = 1 + MAX_RETRIES. Named constant so tests can read it.
MAX_RETRIES: int = 2

# Prefix used to mark a synthetic error string so callers can detect
# the wrapper's own failure responses (distinct from genuine output).
_TASK_ERROR_PREFIX: str = "TASK_ERROR:"


def is_task_error(output: str) -> bool:
    """Return True when ``output`` is a synthetic ``TASK_ERROR:`` string."""
    return bool(output) and output.startswith(_TASK_ERROR_PREFIX)


def format_task_error(exc: BaseException) -> str:
    """Format an exception into a ``TASK_ERROR: <type>: <message>`` string.

    The format is stable: callers can pattern-match ``TASK_ERROR:`` to
    detect a wrapper-generated failure without parsing English.
    """
    return f"{_TASK_ERROR_PREFIX} {type(exc).__name__}: {exc}"


async def run_with_retry(
    task: "AgentTask",
    context: dict[str, "AgentResult"],
    registry,
    runner,
) -> tuple[str, "AgentResult | None", str | None]:
    """Run ``runner`` with up to ``MAX_RETRIES`` retries on failure.

    Args:
        task: The AgentTask to execute.
        context: Upstream results (forwarded unchanged on every attempt).
        registry: Service registry (forwarded to the runner).
        runner: The async runner callable for ``task.role``.

    Returns:
        Tuple of (output, AgentResult-or-None, error-message-or-None).
        On success: ``(output_str, None, None)`` — the AgentResult
        construction is left to the caller (workflow.py has the timing
        and hash logic).
        On failure after exhausting retries: ``(error_str, None, error_str)``
        — the error string is ``TASK_ERROR: ...``.

    Notes:
        - Retries mutate ``task.prompt`` for the duration of the call
          then restore the original on return. No persistent state.
        - ``task.source`` is reset between attempts so the caller's
          source/hash logic still works.
    """
    original_prompt = task.prompt
    last_error: str | None = None
    try:
        for attempt in range(MAX_RETRIES + 1):
            # Reset side effects between attempts — runner sets these.
            task.source = ""
            try:
                output = await runner(task, context, registry)
            except Exception as exc:  # noqa: BLE001
                last_error = format_task_error(exc)
                log.warning(
                    "run_with_retry: task=%s role=%s attempt=%d/%d failed: %s",
                    task.id, task.role, attempt + 1, MAX_RETRIES + 1, exc,
                )
                if attempt < MAX_RETRIES:
                    task.prompt = _append_failure_context(original_prompt, attempt + 1, str(exc))
                    continue
                # Budget exhausted — return the error string.
                return last_error, None, last_error
            # Success — leave prompt restoration to the finally block.
            return output, None, None
        # Defensive: loop should always return.
        return last_error or "TASK_ERROR: unknown", None, last_error
    finally:
        # Always restore the original prompt so a future retry from
        # another caller (or tests) sees a clean state.
        task.prompt = original_prompt


def _append_failure_context(original_prompt: str, attempt_num: int, error: str) -> str:
    """Build the retry prompt: original + a structured failure-context block."""
    snippet = (error or "").strip()[:500]
    return (
        f"{original_prompt}\n\n"
        f"RETRY CONTEXT — Previous attempt {attempt_num} failed with:\n"
        f"{snippet}\n\n"
        f"Try a different approach. If the failure was a tool/timeout, "
        f"avoid the same call. If it was a logic error, re-read the "
        f"objective carefully."
    )
