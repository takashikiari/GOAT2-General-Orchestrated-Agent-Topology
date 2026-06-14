"""GoatContext — pure context builder (NO LLM) of the facts GOAT needs to decide.

Part of the single-call architecture: middleware only assembles context; the one
GOAT decision call (``goat_decision.decide``) does the reasoning. This module
gathers the facts GOAT needs — the workspace root, the agent roles and tools that
actually exist in the registry, and the current memory context — and packages
them into a ``GoatContext``. There is no LLM call, no scoring, and no hardcoded
rules here: roles/tools come dynamically from the registry, the workspace from the
environment.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.supervisor.pipeline.goat_enrichment")

__all__ = ["GoatContext", "build_goat_context"]


@dataclasses.dataclass
class GoatContext:
    """Facts GOAT needs to make its single decision (pure context, no judgment).

    Attributes:
        workspace: Workspace root path from the ``GOAT_WORKSPACE`` env (or "").
        available_agents: Agent roles registered in the registry (dynamic).
        available_tools: Human-readable roles + tool names string (dynamic).
        memory_context: Pre-computed working/episodic memory context for this turn.
    """

    workspace: str
    available_agents: list[str]
    available_tools: str
    memory_context: str

    def to_prompt(self) -> str:
        """Render this context as a prompt block for the GOAT decision call."""
        lines = ["[GOAT capabilities]"]
        if self.workspace:
            lines.append(f"Workspace root (use this exact path): {self.workspace}")
        if self.available_agents:
            lines.append("DAG agent roles: " + ", ".join(self.available_agents))
        if self.available_tools:
            lines.append(self.available_tools)
        if self.memory_context:
            lines.append(f"\n[Memory]\n{self.memory_context}")
        return "\n".join(lines)


def _available_tools(registry: "ServiceRegistry") -> str:
    """Build a human-readable list of available agent roles and tool names.

    Derived dynamically from the registry — no hardcoded agent or tool list.
    Pulls registered DAG roles and the file/memory ToolDefinition names.

    Args:
        registry: ServiceRegistry exposing agent_registry and tool lists.

    Returns:
        A compact descriptive string of available capabilities.
    """
    parts: list[str] = []
    try:
        roles = registry.agent_registry.roles()
        if roles:
            parts.append("Agent roles: " + ", ".join(sorted(roles)))
    except Exception as exc:  # noqa: BLE001 — context-building must never raise
        log.debug("_available_tools: roles() failed: %s", exc)
    for label, attr in (("file tools", "file_tools"), ("memory tools", "memory_tools")):
        try:
            tools = getattr(registry, attr, None) or []
            names = [getattr(t, "name", "") for t in tools if getattr(t, "name", "")]
            if names:
                parts.append(f"{label}: " + ", ".join(names))
        except Exception as exc:  # noqa: BLE001
            log.debug("_available_tools: %s failed: %s", attr, exc)
    return "\n".join(parts)


def _available_agents(registry: "ServiceRegistry") -> list[str]:
    """Return the registered DAG agent roles (sorted), or [] on any error."""
    try:
        return sorted(registry.agent_registry.roles())
    except Exception as exc:  # noqa: BLE001
        log.debug("_available_agents: roles() unavailable: %s", exc)
        return []


def build_goat_context(registry: "ServiceRegistry", mem_ctx: str = "") -> GoatContext:
    """Assemble the GoatContext for this turn — pure, no LLM.

    Args:
        registry: ServiceRegistry for dynamic role/tool discovery.
        mem_ctx: Pre-computed memory context string for this turn.

    Returns:
        A populated GoatContext.
    """
    workspace = os.environ.get("GOAT_WORKSPACE", "")
    ctx = GoatContext(
        workspace=workspace,
        available_agents=_available_agents(registry),
        available_tools=_available_tools(registry),
        memory_context=mem_ctx or "",
    )
    log.debug("build_goat_context: workspace=%s agents=%d", workspace or "(unset)", len(ctx.available_agents))
    return ctx
