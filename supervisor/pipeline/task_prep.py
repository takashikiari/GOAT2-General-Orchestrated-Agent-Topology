"""Prepare AgentTask instances before execution — inject memory_manager and language.

REGISTRY INJECTION (PHASE 4):
=============================
prepare_tasks() now requires `registry` parameter.
Passed to detect_language() for settings access.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

log = logging.getLogger("goat2.supervisor.pipeline")

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from supervisor.types import AgentTask
    from config.registry import Registry

__all__ = ["prepare_tasks"]

_LANG_ROLES: Final[frozenset[str]] = frozenset({"researcher", "coder", "critic", "summarizer"})


async def prepare_tasks(
    tasks: list[AgentTask],
    memory_manager: MemoryManager | None,
    intent: str,
    registry: "Registry",
) -> str:
    """
    Inject memory_manager, language directive, and default source into each task.

    Every task receives:
    - memory_manager for in-task recall.
    - A language directive prepended to the prompt when the user's language is non-English.
    - A fallback source label ('planner') when source is not yet set, satisfying DAG audit
      validation. The runner overwrites this with the real source during execution.

    Returns the detected language string.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Passed to detect_language() for settings access.
    """
    from supervisor.classification.lang_detect import detect_language  # deferred: supervisor→tools→agents cycle
    lang = await detect_language(intent, registry)
    directive = f"Respond in {lang}.\n" if lang.lower() != "english" else ""
    for task in tasks:
        task.memory_manager = memory_manager
        if not task.source:
            task.source = "planner"  # audit placeholder; runner overwrites during execution
        if directive and task.role in _LANG_ROLES:
            task.prompt = f"{directive}{task.prompt}"
    return lang
