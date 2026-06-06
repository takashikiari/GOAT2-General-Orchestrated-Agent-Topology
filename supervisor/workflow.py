from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Final

from config.settings import settings
from supervisor.types import AgentTask, AgentResult, TaskStatus
from supervisor.registry import AgentRegistry
from supervisor.llm_utils import _model_label

_SOURCE_TOOL: Final[dict[str, str]] = {
    "net": "web_search", "file": "file_read",
    "memory": "memory_search", "generated": "",
}

log = logging.getLogger("goat2.supervisor")

__all__ = ["WorkflowGraph"]


class WorkflowGraph:
    """
    Executes an AgentTask DAG in topological waves via Kahn's algorithm.
    Tasks in the same wave run concurrently, bounded by a shared semaphore.
    """

    def __init__(self, tasks: list[AgentTask]) -> None:
        self.tasks: dict[str, AgentTask] = {t.id: t for t in tasks}

    def topological_waves(self) -> list[list[str]]:
        """Group task IDs into parallel waves; raises ValueError on cycles or unknown deps."""
        in_degree: dict[str, int] = {tid: 0 for tid in self.tasks}
        dependents: dict[str, list[str]] = {tid: [] for tid in self.tasks}
        for task in self.tasks.values():
            for dep in task.depends_on:
                if dep not in self.tasks:
                    raise ValueError(f"Task '{task.id}' depends on unknown task '{dep}'")
                in_degree[task.id] += 1
                dependents[dep].append(task.id)
        waves: list[list[str]] = []
        ready = [tid for tid, deg in in_degree.items() if deg == 0]
        while ready:
            waves.append(list(ready))
            nxt: list[str] = []
            for tid in ready:
                for child in dependents[tid]:
                    in_degree[child] -= 1
                    GOAT 2.0 — intent: %.120s", intent)

        if self._