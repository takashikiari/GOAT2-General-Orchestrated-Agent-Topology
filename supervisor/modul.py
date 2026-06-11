"""modul — Reusable agent module definitions for GOAT 2.0 supervisor.

Provides abstract base classes and concrete implementations for agent
modules that can be plugged into the DAG pipeline. Each module wraps a
specific capability (research, coding, critique, summarization, etc.)
and conforms to the AgentRunner protocol.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("goat2.supervisor.modul")

__all__ = [
    "AgentModule",
    "ModuleResult",
    "ModuleRegistry",
    "ResearchModule",
    "CodingModule",
    "CritiqueModule",
    "SummarizerModule",
]


@dataclass
class ModuleResult:
    """
    Result produced by an AgentModule execution.

    Attributes:
        module_name: Name of the module that produced this result.
        output: The primary output text.
        metadata: Arbitrary extra data (tokens used, sources, etc.).
        success: Whether execution completed without error.
        error: Error message if success is False.
    """

    module_name: str
    output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str = ""


class AgentModule(abc.ABC):
    """
    Abstract base class for all agent modules.

    Subclasses must implement ``execute`` and provide a unique ``name``.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        """Human-readable module name."""
        return self._name

    @abc.abstractmethod
    async def execute(self, task: Any, context: dict[str, Any]) -> ModuleResult:
        """
        Execute the module's core logic.

        Args:
            task: The task descriptor (typically an AgentTask or DAGNode).
            context: Results from upstream dependencies keyed by node ID.

        Returns:
            A ModuleResult with the output and metadata.
        """
        ...


class ModuleRegistry:
    """
    Registry that maps role names to AgentModule instances.

    Provides lookup and lifecycle management for all available modules.
    """

    def __init__(self) -> None:
        self._modules: dict[str, AgentModule] = {}

    def register(self, module: AgentModule) -> None:
        """Register a module under its role name."""
        if module.name in self._modules:
            log.warning("Overwriting existing module: '%s'", module.name)
        self._modules[module.name] = module
        log.debug("Registered module: '%s'", module.name)

    def get(self, role: str) -> AgentModule | None:
        """Retrieve a module by role name, or None if not found."""
        return self._modules.get(role)

    def has(self, role: str) -> bool:
        """Check if a module is registered for the given role."""
        return role in self._modules

    def list_roles(self) -> list[str]:
        """Return all registered role names."""
        return list(self._modules.keys())

    def __repr__(self) -> str:
        return f"<ModuleRegistry roles={self.list_roles()}>"


# ------------------------------------------------------------------
# Concrete module implementations
# ------------------------------------------------------------------


class ResearchModule(AgentModule):
    """
    Module that performs web research and information gathering.

    Uses web search tools to collect relevant data for a given query.
    """

    def __init__(self) -> None:
        super().__init__("researcher")

    async def execute(self, task: Any, context: dict[str, Any]) -> ModuleResult:
        log.info("ResearchModule executing task '%s'", task.node_id if hasattr(task, "node_id") else task)
        # In production this would call web_search / LLM.
        return ModuleResult(
            module_name=self.name,
            output=f"Research results for task: {task}",
            metadata={"sources": []},
        )


class CodingModule(AgentModule):
    """
    Module that generates, reviews, or modifies source code.

    Delegates to a code-generation LLM and returns file diffs or new code.
    """

    def __init__(self) -> None:
        super().__init__("coder")

    async def execute(self, task: Any, context: dict[str, Any]) -> ModuleResult:
        log.info("CodingModule executing task '%s'", task.node_id if hasattr(task, "node_id") else task)
        return ModuleResult(
            module_name=self.name,
            output=f"Generated code for: {task}",
            metadata={"language": "python", "files": []},
        )


class CritiqueModule(AgentModule):
    """
    Module that reviews and critiques outputs from other modules.

    Evaluates quality, correctness, and completeness of upstream results.
    """

    def __init__(self) -> None:
        super().__init__("critic")

    async def execute(self, task: Any, context: dict[str, Any]) -> ModuleResult:
        log.info("CritiqueModule reviewing %d upstream results", len(context))
        feedback_parts: list[str] = []
        for node_id, result in context.items():
            if hasattr(result, "output"):
                feedback_parts.append(f"[{node_id}]: {result.output[:200]}")
        return ModuleResult(
            module_name=self.name,
            output="Critique feedback:\n" + "\n".join(feedback_parts),
            metadata={"reviewed_nodes": list(context.keys())},
        )


class SummarizerModule(AgentModule):
    """
    Module that synthesizes multiple results into a coherent summary.

    Combines outputs from all upstream nodes into a final response.
    """

    def __init__(self) -> None:
        super().__init__("summarizer")

    async def execute(self, task: Any, context: dict[str, Any]) -> ModuleResult:
        log.info("SummarizerModule synthesizing %d results", len(context))
        combined = "\n\n".join(
            f"### {nid}\n{r.output}" for nid, r in context.items() if hasattr(r, "output")
        )
        return ModuleResult(
            module_name=self.name,
            output=combined or "No upstream results to summarize.",
            metadata={"input_count": len(context)},
        )
