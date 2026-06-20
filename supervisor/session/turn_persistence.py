"""Turn persistence — store a completed turn to working memory,
trigger style analysis, schedule tier promotion, refresh the
in-memory style cache. Free functions over the live supervisor
instance (no singletons, no module-level state).

USAGE (from the supervisor):
    from supervisor.session.turn_persistence import store_and_promote

    await store_and_promote(supervisor, turn_count, intent, summary)

WHAT IT DOES:
  1. Stores the turn in working memory as a structured record
     (``turn:<n>`` key).
  2. Analyzes recent user turns and persists an updated style
     profile to Letta's ``persona`` block.
  3. If the style was actually written, refreshes the
     supervisor's in-memory ``_behavior_style`` so the next
     turn's system prompt sees the freshest style.
  4. Schedules the background tier-promotion task.

All steps degrade quietly on error so a memory hiccup never
breaks the turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Final

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor

log = logging.getLogger("goat2.supervisor.session.turn_persistence")

__all__ = [
    "store_and_promote",
    "schedule_promotion",
    "store_action_log",
    "format_action_log",
    "_action_log_from_turn",
    "_ACTION_LOG_KEY",
    "_ACTION_SUMMARY_CAP",
]

# How many recent working-memory entries the analyzer reads.
# Small window keeps the analyzer fast (O(n) scoring on a
# bounded list) and avoids stale turn influence.
_ANALYZER_WINDOW: Final[int] = 10


# ── Action log: structured per-tool record for self-reporting ────────────

# Key under which the per-turn action log is persisted. Sibling of
# ``turn:<N>:intent`` and ``turn:<N>:summary``. Value is a small
# JSON list; one entry per tool call, with success flag and a
# short summary of the result.
_ACTION_LOG_KEY: Final[str] = "turn:{n}:actions"

# Per-entry summary cap. Matches layer_renderer's working-memory
# truncation so the action log fits the same prompt budget.
_ACTION_SUMMARY_CAP: int = 200


def _action_log_from_turn(turn) -> list[dict]:
    """Build the structured action-log entries for one turn.

    Each entry is a dict with: ``tool`` (name), ``args`` (dict of
    arguments the model passed — best-effort parsed), ``ok``
    (bool), ``summary`` (first N chars of the tool result).

    Failure detection: a result starting with ``ERROR`` is
    treated as ok=False. We also catch common error prefixes
    that memory tools emit (e.g. "Key not found:", "ERROR calling...",
    "Connection refused:") so the structured log flags the failure
    for the next turn's self-report.
    """
    called = tuple(getattr(turn, "called_tools", ()) or ())
    results = tuple(getattr(turn, "tool_results", ()) or ())
    entries: list[dict] = []
    for tool, raw_result in zip(called, results):
        result_str = str(raw_result or "")
        # Heuristic error patterns. Each entry matches a common
        # memory-tool error shape; the list is short on purpose
        # — false positives are tolerable (the next turn just
        # sees a "FAIL" for an entry that actually succeeded, which
        # is honest but slightly over-cautious).
        error_prefixes = (
            "ERROR:",
            "ERROR ",
            "Error:",
            "Key not found",
            "No entry found",
            "Connection refused",
            "Connection error",
        )
        ok = not any(result_str.startswith(p) for p in error_prefixes)
        # Strip a leading "ERROR: " for a cleaner summary line.
        summary = result_str
        if summary.startswith("ERROR:"):
            summary = summary[len("ERROR:"):].lstrip()
        elif summary.startswith("ERROR "):
            summary = summary[len("ERROR "):].lstrip()
        elif summary.startswith("Error:"):
            summary = summary[len("Error:"):].lstrip()
        entries.append({
            "tool": tool,
            "args": {},  # args were passed to the tool but not echoed
                          # back on the result; we leave this empty
                          # rather than guess. The intent layer can
                          # fill it in if needed.
            "ok": ok,
            "summary": summary[:_ACTION_SUMMARY_CAP],
        })
    return entries


def format_action_log(entries: list[dict]) -> str:
    """Render the structured action log as human-readable lines.

    Format per line::

        tool_name → ok: <one-line summary>
        tool_name → FAIL: <one-line summary>

    The visual ``ok`` / ``FAIL`` distinction lets the model
    report successes and failures correctly without parsing.
    Long summaries are truncated to ``_ACTION_SUMMARY_CAP``
    chars so a single verbose tool result doesn't blow the
    prompt budget.
    """
    if not entries:
        return ""
    lines: list[str] = []
    for e in entries:
        tool = e.get("tool", "?")
        summary = e.get("summary", "")
        # Defensive truncation — entries produced by
        # _action_log_from_turn are already truncated, but a
        # caller could pass pre-built entries from elsewhere.
        if len(summary) > _ACTION_SUMMARY_CAP:
            summary = summary[:_ACTION_SUMMARY_CAP]
        status = "ok" if e.get("ok") else "FAIL"
        args = e.get("args", {}) or {}
        arg_str = ""
        if args:
            # Compact rendering: key1=val1, key2=val2
            arg_str = " " + ", ".join(
                f"{k}={v}" for k, v in list(args.items())[:3]
            )
        lines.append(f"- {tool}{arg_str} → {status}: {summary}")
    return "\n".join(lines)


async def store_action_log(
    mm,
    turn_count: int,
    turn,
) -> None:
    """Persist the structured action log for one turn.

    Writes a single record under ``turn:<n>:actions`` containing
    a JSON-encoded list of entries. Best-effort: any failure is
    logged at DEBUG (not WARNING) because losing one log entry
    is not a turn-breaking failure — the model just falls back
    to its previous behaviour of not having structured data.

    Skips silently when ``called_tools`` is empty (the turn had
    no tool calls — direct reply).
    """
    if mm is None:
        return
    called = tuple(getattr(turn, "called_tools", ()) or ())
    if not called:
        return
    try:
        entries = _action_log_from_turn(turn)
        if not entries:
            return
        key = _ACTION_LOG_KEY.format(n=turn_count)
        payload = json.dumps(entries, ensure_ascii=False)
        await mm.store(SESSION_ROLE, key, payload)
        log.debug("store_action_log: turn %d actions stored (%d entries)",
                  turn_count, len(entries))
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.debug("store_action_log failed (turn=%d): %s", turn_count, exc)


async def store_and_promote(
    supervisor: "GoatSupervisor",
    turn_count: int,
    intent: str,
    summary: str,
) -> None:
    """Persist the turn, learn style, refresh cache, schedule promotion.

    Args:
        supervisor: The live GoatSupervisor (source of mm, registry).
        turn_count: 1-based turn number (``len(history.messages)``).
        intent: The raw user intent for this turn.
        summary: The assistant's user-facing summary for this turn.

    Returns:
        None. Best-effort; never raises.
    """
    mm = getattr(supervisor, "memory_manager", None)
    if mm is None:
        return
    try:
        # 1. Persist this exchange to working memory.
        await _store_turn(mm, turn_count, intent, summary)
        log.debug("store_and_promote: turn %d persisted", turn_count)

        # 2. Persist the structured action log so the NEXT turn
        #    can report from data (tool calls + outcomes) rather
        #    than confabulate from its own previous text.
        if getattr(supervisor, "_last_turn_result", None) is not None:
            await store_action_log(
                mm, turn_count, supervisor._last_turn_result,
            )

        # 3. Behavioral learning — analyze + write + cache refresh.
        style_was_written = await _learn_and_persist(supervisor, mm)
        if style_was_written:
            # 3. Refresh the in-memory style cache so the next
            #    turn's system prompt sees the freshest profile.
            from supervisor.mechanisms.style_sync import refresh_style
            await refresh_style(supervisor)

        # 4. Schedule the background tier promotion.
        # NOTE: schedule_promotion is itself responsible for
        # creating its own asyncio task and registering it in
        # supervisor._background_tasks (BUG-027). Do NOT wrap
        # it in asyncio.create_task here — that would pass
        # schedule_promotion's None return value to create_task,
        # which raises "a coroutine was expected, got None".
        schedule_promotion(supervisor, turn_count)
    except Exception as exc:  # noqa: BLE001 — never break the turn
        log.warning("store_and_promote failed: %s", exc)


async def _store_turn(
    mm,
    turn_count: int,
    intent: str,
    summary: str,
) -> None:
    """Store one turn as two separate working-memory records.

    The intent and the assistant summary are written under distinct keys
    so the style analyzer (``_learn_and_persist``) can train on user
    input alone. Bundling them into a single payload would mix prior
    GOAT responses with user input, biasing the learned style profile
    toward the assistant's voice instead of the user's.

    Args:
        mm: MemoryManager.
        turn_count: 1-based turn number.
        intent: Raw user intent for this turn.
        summary: Assistant's user-facing summary for this turn.

    Returns:
        None. Best-effort; never raises.
    """
    try:
        await mm.store(SESSION_ROLE, f"turn:{turn_count}:intent", intent or "")
        await mm.store(SESSION_ROLE, f"turn:{turn_count}:summary", summary or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("_store_turn failed: %s", exc)


# How many recent user-intent entries the analyzer reads.
# Doubled vs the legacy window so the ``e.key.endswith(":intent")`` filter
# still yields ≥ ``min_turns_to_learn`` samples after dropping summaries.
_INTENT_WINDOW: Final[int] = _ANALYZER_WINDOW * 2


async def _learn_and_persist(supervisor: "GoatSupervisor", mm) -> bool:
    """Run the analyzer, write to Letta, return True on successful write.

    Steps:
        1. Load the existing persona block from Letta (the merged baseline
           against which the new style will be diffed). Without this read,
           every turn overwrites the profile with a standalone one and
           incremental learning is lost.
        2. Read recent user-intent entries (key suffix ``:intent`` only —
           never the assistant summaries, which would bias the profile).
        3. Call ``analyze_style(user_turns, existing)`` so the new profile
           merges over the old.
        4. Write the merged profile back to Letta.

    Args:
        supervisor: The live GoatSupervisor (for ``mm`` access).
        mm: The registry's MemoryManager.

    Returns:
        True when a new profile was written, False on any failure or when
        the analyzer returns empty text.
    """
    try:
        from supervisor.behavior.style_learner import analyze_style
        from supervisor.behavior.store import load_style, save_style
        existing = await load_style(mm) or ""
        entries = await mm.working.list(SESSION_ROLE, limit=_INTENT_WINDOW)
        user_turns = [
            e.content for e in entries
            if e and e.content and getattr(e, "key", "").endswith(":intent")
        ]
        if not user_turns:
            return False
        new_text = await analyze_style(user_turns, existing)
        if not new_text:
            return False
        return bool(await save_style(mm, new_text))
    except Exception as exc:  # noqa: BLE001
        log.debug("_learn_and_persist failed: %s", exc)
        return False


async def _do_promote(supervisor: "GoatSupervisor", turn_count: int) -> None:
    """Run the actual ``mm.promote_turns`` call. Body of the
    background task — split out so the wrapper can wrap it in
    error logging + task registration."""
    mm = getattr(supervisor, "memory_manager", None)
    if mm is None:
        return
    await mm.promote_turns(SESSION_ROLE, turn_count)


def schedule_promotion(supervisor: "GoatSupervisor", turn_count: int) -> None:
    """Promote conversation turns through memory tiers (background).

    BUG-027 fix: the task is now registered on
    ``supervisor._background_tasks`` (key ``turn-promotion:<n>``)
    so it can be awaited at session end. Exceptions inside the
    task are logged at WARNING (not DEBUG) so recurring failures
    are visible. The function itself is sync (fire-and-forget);
    awaiting the actual work is the task's job.
    """
    mm = getattr(supervisor, "memory_manager", None)
    if mm is None:
        return
    registry = getattr(supervisor, "_background_tasks", None)
    if registry is None:
        # No registry available — fall back to the legacy
        # detached behaviour. The task is fire-and-forget and
        # exceptions are silently lost. This path is only used
        # by tests that build a bare supervisor stub.
        try:
            asyncio.create_task(_do_promote(supervisor, turn_count))
        except RuntimeError:
            # No event loop — give up silently.
            pass
        return
    key = f"turn-promotion:{turn_count}"

    async def _runner() -> None:
        try:
            await _do_promote(supervisor, turn_count)
        except Exception as exc:  # noqa: BLE001
            log.warning("schedule_promotion failed (turn=%d): %s", turn_count, exc)
        finally:
            # Always remove the task from the registry on exit so
            # finalize_background_tasks can drain cleanly.
            registry.pop(key, None)

    try:
        task = asyncio.create_task(_runner(), name=key)
    except RuntimeError:
        # No event loop running — cannot schedule.
        log.debug("schedule_promotion: no running event loop — skipping turn=%d", turn_count)
        return
    registry[key] = task
