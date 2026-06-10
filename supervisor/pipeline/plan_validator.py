"""Plan validation for GOAT 2.0 — validates Plan objects before DAG execution.

Ensures structural integrity of the task DAG: unique IDs, valid roles,
correct dependency references, acyclicity, and non-empty plan.
"""
from __future__ import annotations

import logging
from typing import Final

from supervisor.types import Plan

log = logging.getLogger("goat2.supervisor")

# ── Valid agent roles (must match AgentModels fields and PLANNER_SYSTEM) ──
VALID_ROLES: Final[frozenset[str]] = frozenset({
    "researcher",
    "coder",
    "critic",
    "summarizer",
    "tool_caller",
    "memory",
})


def validate_plan(plan: Plan) -> tuple[bool, list[str], list[str]]:
    """Validate a Plan object before it is passed to WorkflowGraph.

    Checks performed:
      1. All task IDs are unique (no duplicates).
      2. Every task has a non-empty ``id`` and ``role``.
      3. Every role exists in the agent registry (VALID_ROLES).
      4. All ``depends_on`` references point to existing task IDs.
      5. No circular dependencies (cycle detection via DFS).
      6. At least one task exists (plan is not empty).
      7. A final summarizer task exists and depends on all other tasks
         (soft warning, not a hard error).

    Args:
        plan: The Plan object to validate.

    Returns:
        A tuple ``(is_valid, errors, warnings)`` where:
        - ``is_valid`` is ``True`` when there are zero hard errors.
        - ``errors`` is a list of hard error messages (invalidates the plan).
        - ``warnings`` is a list of soft warning messages (advisory only).
    """
    errors: list[str] = []
    warnings: list[str] = []

    tasks = plan.tasks

    # ── Check 6: Plan is not empty ──────────────────────────────────────
    if not tasks:
        errors.append("Plan is empty — no tasks defined.")
        return False, errors, warnings

    # ── Check 1: Unique task IDs ────────────────────────────────────────
    seen_ids: dict[str, int] = {}
    for i, task in enumerate(tasks):
        if task.id in seen_ids:
            errors.append(
                f"Duplicate task ID '{task.id}' at indices "
                f"{seen_ids[task.id]} and {i}."
            )
        else:
            seen_ids[task.id] = i

    # ── Check 2: Non-empty id and role ──────────────────────────────────
    for task in tasks:
        if not task.id or not task.id.strip():
            errors.append(f"Task at index {tasks.index(task)} has an empty 'id'.")
        if not task.role or not task.role.strip():
            errors.append(
                f"Task '{task.id or '<empty>'}' has an empty 'role'."
            )

    # ── Check 3: Valid roles ────────────────────────────────────────────
    for task in tasks:
        role = task.role.strip() if task.role else ""
        if role and role not in VALID_ROLES:
            errors.append(
                f"Task '{task.id}' has unknown role '{role}'. "
                f"Valid roles: {', '.join(sorted(VALID_ROLES))}."
            )

    # ── Check 4: depends_on references exist ────────────────────────────
    all_ids: set[str] = {t.id for t in tasks if t.id and t.id.strip()}
    for task in tasks:
        if not task.id or not task.id.strip():
            continue  # already reported above
        for dep in task.depends_on:
            if dep not in all_ids:
                errors.append(
                    f"Task '{task.id}' depends on '{dep}', "
                    f"but no task with that ID exists in the plan."
                )

    # ── Check 5: Circular dependencies (cycle detection) ────────────────
    # Build adjacency list from valid tasks only
    adj: dict[str, list[str]] = {tid: [] for tid in all_ids}
    for task in tasks:
        if task.id in adj:
            for dep in task.depends_on:
                if dep in adj:
                    adj[task.id].append(dep)

    cycle = _find_cycle(adj)
    if cycle:
        errors.append(
            f"Circular dependency detected: {' → '.join(cycle)}."
        )

    # ── Check 7: Final summarizer exists and depends on all tasks ───────
    summarizer_tasks = [t for t in tasks if t.role == "summarizer"]
    if not summarizer_tasks:
        warnings.append(
            "No summarizer task found in the plan. "
            "Consider adding a final summarizer that depends on all other tasks."
        )
    else:
        # Use the last summarizer in the list as the "final" one
        final_summarizer = summarizer_tasks[-1]
        other_ids = all_ids - {final_summarizer.id}
        missing_deps = other_ids - set(final_summarizer.depends_on)
        if missing_deps:
            warnings.append(
                f"Final summarizer task '{final_summarizer.id}' does not depend on "
                f"all other tasks. Missing dependencies: {', '.join(sorted(missing_deps))}."
            )

    is_valid = len(errors) == 0
    return is_valid, errors, warnings


# ── Cycle detection (DFS-based) ──────────────────────────────────────────


def _find_cycle(adj: dict[str, list[str]]) -> list[str] | None:
    """Detect a directed cycle using DFS with ancestor tracking.

    Args:
        adj: Adjacency list mapping node_id → list of dependency node_ids
             (edge direction: node depends on each dependency).

    Returns:
        A list of node IDs forming a cycle (e.g. ``['a', 'b', 'a']``),
        or ``None`` if the graph is acyclic.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in adj}
    parent: dict[str, str | None] = {n: None for n in adj}

    def dfs(node: str) -> list[str] | None:
        """DFS that returns a cycle path if found, else None."""
        color[node] = GRAY
        for neighbour in adj[node]:
            if color.get(neighbour, WHITE) == GRAY:
                # Found a back edge → cycle detected
                cycle = [neighbour, node]
                cur = node
                while cur != neighbour and parent[cur] is not None:
                    cur = parent[cur]  # type: ignore[assignment]
                    if cur is not None:
                        cycle.append(cur)
                cycle.reverse()
                return cycle
            if color.get(neighbour, WHITE) == WHITE:
                parent[neighbour] = node
                result = dfs(neighbour)
                if result is not None:
                    return result
        color[node] = BLACK
        return None

    for node in adj:
        if color[node] == WHITE:
            result = dfs(node)
            if result is not None:
                return result
    return None
