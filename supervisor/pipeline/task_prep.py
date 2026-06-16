"""Prepare AgentTask instances before execution — inject memory_manager and language.

The language directive is the only user-facing output of the (now
heuristic) ``detect_language`` call. Default to no directive for
``"en"``; prepend a ``Respond in <lang>.`` line otherwise.
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

# Short language codes that trigger a directive. Anything else
# (including the catch-all ``"en"`` and ``"mixed"``) is left alone
# so the LLM can answer in the user's own mix.
_LANG_DIRECTIVE: Final[dict[str, str]] = {
    "ro": "Romanian",
}


async def prepare_tasks(
    tasks: list[AgentTask],
    memory_manager: MemoryManager | None,
    intent: str,
    registry: "Registry",
) -> str:
    """Inject memory_manager, language directive, and default source into each task.

    Every task receives:
    - memory_manager for in-task recall.
    - A language directive prepended to the prompt when the user's
      language is non-English (currently only ``ro`` triggers a
      directive; ``en`` and ``mixed`` skip the directive so the
      LLM answers in whatever mix the user prefers).
    - A fallback source label ('planner') when source is not yet
      set, satisfying DAG audit validation. The runner overwrites
      this with the real source during execution.

    Returns the detected language short code (``"ro"`` | ``"en"``
    | ``"mixed"``) — kept for callers that log it. The detection
    itself is heuristic (no LLM) and lives in
    ``supervisor.classification.lang_detect``.
    """
    from supervisor.classification.lang_detect import detect_language  # deferred: supervisor→tools→agents cycle
    lang = detect_language(intent)
    directive = (
        f"Respond in {_LANG_DIRECTIVE[lang]}.\n"
        if lang in _LANG_DIRECTIVE else ""
    )
    for task in tasks:
        task.memory_manager = memory_manager
        if not task.source:
            task.source = "planner"  # audit placeholder; runner overwrites during execution
        if directive and task.role in _LANG_ROLES:
            task.prompt = f"{directive}{task.prompt}"
    return lang
