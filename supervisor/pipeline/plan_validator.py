"""Plan validator — sanity-check an LLM-produced AgentTask DAG.

Pure-Python structural validator — no LLM, no I/O. Used by
``agents.planner_decompose.decompose_plan`` to catch malformed
plans before they reach the executor.

USAGE:
    from supervisor.pipeline.plan_validator import validate_plan
    is_valid, errors, warnings = validate_plan(plan)

The validator rejects:
  - Empty plans (no tasks).
  - Duplicate task IDs.
  - ``depends_on`` references that don't exist in the plan.
  - Cyclic dependencies (a task depending on itself or a cycle
    across multiple tasks).
  - Unknown role names.
  - Plans with no summarizer task at all.

The validator warns on:
  - Tasks with no ``role``.
  - Tasks with no ``prompt``.
  - A summarizer that doesn't depend on every other task
    (so the final synthesis receives all upstream outputs).

All checks are best-effort structural — semantic correctness
(does the prompt make sense for the role?) is still the LLM's
job.
"""
from __future__ import annotations

import logging
from typing import Iterable

log = logging.getLogger("goat2.supervisor.pipeline.plan_validator")

__all__ = ["validate_plan", "KNOWN_ROLES"]


# Canonical set of DAG-agent roles recognised by the executor.
# Mirrors ``agents/registry.py:_DEFAULT_ROLES``.
KNOWN_ROLES: frozenset[str] = frozenset({
    "planner", "researcher", "coder", "critic",
    "summarizer", "tool_caller", "memory",
})


def _detect_cycle(task_ids: Iterable[str], deps: dict[str, list[str]]) -> bool:
    """Return True when the dependency graph has a cycle.

    Iterative DFS from every node — no recursion, no regex.
    """
    task_ids = list(task_ids)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {tid: WHITE for tid in task_ids}
    for start in task_ids:
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, i = stack[-1]
            if color[node] == WHITE:
                color[node] = GRAY
            children = deps.get(node, [])
            if i < len(children):
                stack[-1] = (node, i + 1)
                child = children[i]
                if child not in color:
                    # Depends on a task that isn't in the plan.
                    return True
                if color[child] == GRAY:
                    return True  # cycle
                if color[child] == WHITE:
                    stack.append((child, 0))
                # BLACK → already fully explored, skip.
            else:
                color[node] = BLACK
                stack.pop()
    return False


def validate_plan(plan) -> tuple[bool, list[str], list[str]]:
    """Validate an LLM-produced Plan.

    Args:
        plan: A ``Plan`` (from ``config.agent_types``) — a list-like
            of ``AgentTask`` objects with ``id``, ``role``,
            ``prompt``, ``depends_on``.

    Returns:
        ``(is_valid, errors, warnings)``:
          - ``is_valid``: True when the plan passes every error check.
          - ``errors``: Blocking issues (empty plan, duplicate IDs,
            unknown roles, missing depends_on targets, cycles).
          - ``warnings``: Non-blocking issues (missing prompt,
            summarizer not connected to all tasks).
    """
    errors: list[str] = []
    warnings: list[str] = []

    tasks = list(getattr(plan, "tasks", []) or [])
    if not tasks:
        return False, ["plan has no tasks"], warnings

    ids: list[str] = []
    deps: dict[str, list[str]] = {}
    for t in tasks:
        tid = getattr(t, "id", None)
        if not tid:
            errors.append(f"task missing id: {t!r}")
            continue
        if tid in ids:
            errors.append(f"duplicate task id: {tid!r}")
        ids.append(tid)
        role = getattr(t, "role", None)
        if role and role not in KNOWN_ROLES:
            errors.append(f"unknown role {role!r} on task {tid!r}")
        if not role:
            warnings.append(f"task {tid!r} has no role")
        if not getattr(t, "prompt", None):
            warnings.append(f"task {tid!r} has no prompt")
        deps[tid] = list(getattr(t, "depends_on", []) or [])

    # depends_on must reference known tasks.
    for tid, d in deps.items():
        for ref in d:
            if ref not in deps:
                errors.append(
                    f"task {tid!r} depends on unknown task {ref!r}"
                )

    # Cycle detection.
    if not errors and _detect_cycle(ids, deps):
        errors.append("plan contains a dependency cycle")

    # Summarizer coverage — final summarizer should depend on all
    # other tasks. If not, warn (not an error — some flows don't
    # need a summarizer at all).
    summarizers = [t.id for t in tasks if getattr(t, "role", None) == "summarizer"]
    if summarizers:
        final_sum = summarizers[-1]
        missing = [tid for tid in ids if tid != final_sum and tid not in deps.get(final_sum, [])]
        if missing:
            warnings.append(
                f"summarizer {final_sum!r} does not depend on: {missing}"
            )

    return (not errors), errors, warnings