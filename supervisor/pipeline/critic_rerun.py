"""Critic fallback: re-execute MAJOR/CRITICAL tasks with stricter prompts."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from supervisor.types import AgentResult, Plan

if TYPE_CHECKING:
    from asyncio import Semaphore
    from memory.shared import MemoryManager
    from config.registry import ServiceRegistry
    from agents.critique import CriticVerdict

log = logging.getLogger("goat2.supervisor.pipeline")

__all__ = ["STRICTER_SYSTEM_PROMPTS", "_rerun_failed_tasks"]

STRICTER_SYSTEM_PROMPTS: dict[str, str] = {
    "researcher": (
        "You are a deep research agent. RE-EXECUTION: your previous output was flagged "
        "as insufficient by the critic. Be MORE thorough. Use web_search(query) for EVERY "
        "claim that needs verification. Cross-reference multiple sources. "
        "Output structured findings with explicit citations."
    ),
    "coder": (
        "Expert software engineer. RE-EXECUTION: your previous code was flagged as "
        "problematic by the critic. Be MORE careful. Read files before writing. "
        "Verify your logic. Add error handling. Write clean typed code in fenced blocks."
    ),
    "tool_caller": (
        "Tool orchestration agent. RE-EXECUTION: your previous execution was flagged "
        "as insufficient by the critic. Be MORE thorough. Use the right tools for each step. "
        "File tools: file_read, file_write, file_create, file_list, file_search, "
        "file_grep(path, pattern), file_info(path), file_read_lines(path, start_line, end_line). "
        "Search: web_search. Memory: memory_search, memory_get, memory_store. "
        "Say 'tool not connected' on ERROR. Never ask user to run shell commands."
    ),
}


async def _rerun_failed_tasks(
    plan: Plan,
    results: dict[str, AgentResult],
    registry: "ServiceRegistry",
    semaphore: "Semaphore",
    memory_manager: "MemoryManager | None",
    session_id: str,
    verdict: "CriticVerdict",
) -> dict[str, AgentResult]:
    """Re-execute MAJOR/CRITICAL tasks with stricter prompts; keeps existing passing results."""
    log.info("Critic fallback: severity=%s, re-executing failing tasks", verdict.severity)
    rerun_tasks = [
        t for t in plan.tasks
        if t.role in STRICTER_SYSTEM_PROMPTS and t.id in results and results[t.id].error is None
    ]
    if not rerun_tasks:
        log.info("Critic fallback: no rerunnable tasks found")
        return results
    for task in rerun_tasks:
        original_prompt = task.prompt
        override = STRICTER_SYSTEM_PROMPTS.get(task.role, "")
        task.prompt = (
            f"[STRICT RE-EXECUTION] {override}\n\nOriginal task: {original_prompt}" if override
            else f"[STRICT RE-EXECUTION] Be more thorough.\n\nOriginal task: {original_prompt}"
        )
        context = {
            dep_id: results[dep_id] for dep_id in task.depends_on
            if dep_id in results and results[dep_id].error is None
        }
        async with semaphore:
            task.memory_manager = memory_manager
            t_start = time.monotonic()
            try:
                output = await registry.get(task.role)(task, context, registry)
                dur = time.monotonic() - t_start
                results[task.id] = AgentResult(
                    task_id=task.id, role=task.role, output=output, model="",
                    duration_s=dur, error=None, source=task.source,
                    tool_called=False, tool_name="", raw_output_hash="",
                )
                log.info("Rerun task %s (%s): OK (%.1fs)", task.id, task.role, dur)
            except Exception as e:
                dur = time.monotonic() - t_start
                log.exception("Rerun task %s failed", task.id)
                results[task.id] = AgentResult(
                    task_id=task.id, role=task.role, output="", model="",
                    duration_s=dur, error=str(e), source=task.source,
                    tool_called=False, tool_name="", raw_output_hash="",
                )
        task.prompt = original_prompt
    return results
