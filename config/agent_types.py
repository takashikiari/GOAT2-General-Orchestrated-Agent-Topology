"""config.agent_types — shared data types for the multi-agent pipeline.

These types flow between the DAG engine, agent runners, and the supervisor.
No imports from agents/ or tools/ — only stdlib.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

__all__ = ["AgentTask", "AgentResult", "AgentRunner", "Plan"]


@dataclasses.dataclass
class AgentTask:
    """Task specification passed to an agent node."""

    id: str
    role: str
    prompt: str
    depends_on: list[str] = dataclasses.field(default_factory=list)
    source: str = "generated"


@dataclasses.dataclass
class AgentResult:
    """Output from a completed agent task."""

    role: str
    output: str
    ok: bool = True
    error: str = ""


@dataclasses.dataclass
class Plan:
    """Decomposed execution plan produced by the planner agent."""

    tasks: list[AgentTask] = dataclasses.field(default_factory=list)


# Type alias for agent callable protocol
AgentRunner = Callable[["AgentTask", dict[str, AgentResult]], Awaitable[str]]
