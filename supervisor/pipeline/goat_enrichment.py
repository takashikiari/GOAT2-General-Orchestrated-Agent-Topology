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
    dag_tools: list[str] = None
    has_prior_knowledge: bool = False
    project_structure: str = ""

    def to_prompt(self) -> str:
        """Render this context as a prompt block for the GOAT decision call."""
        lines = ["[GOAT capabilities]"]
        if self.workspace:
            lines.append(f"Workspace root (use this exact path): {self.workspace}")
        if self.available_agents:
            lines.append("DAG agent roles: " + ", ".join(self.available_agents))
        if self.dag_tools:
            lines.append("DAG tool_caller tools (file operations): " + ", ".join(self.dag_tools))
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


def _format_profile_block(profile: dict) -> str:
    """Render a BehaviorProfile dict as a 4-line '[User Style Profile]' block.

    Pure-Python; no LLM. Returns '' when the profile is empty so the
    caller can skip the header entirely.
    """
    if not profile:
        return ""
    lines = ["[User Style Profile]"]
    for field in ("formality", "tone", "vocabulary", "language", "humor", "length"):
        value = profile.get(field)
        if value:
            lines.append(f"- {field}: {value}")
    notes = profile.get("notes")
    if notes:
        lines.append(f"- notes: {notes}")
    return "\n".join(lines)


async def build_goat_context(registry: "ServiceRegistry", mem_ctx: str = "") -> GoatContext:
    """Assemble the GoatContext for this turn — pure, no LLM.

    Args:
        registry: ServiceRegistry for dynamic role/tool discovery and
            for loading the active behavior-style profile from Letta.
        mem_ctx: Pre-computed memory context string for this turn.

    Returns:
        A populated GoatContext with the active profile appended to
        ``memory_context`` as a ``[User Style Profile]`` block. The
        profile is loaded via ``behavior_session.get_profile``; when
        Letta is unreachable or the profile is empty, no block is
        added (GOAT falls back to default tone).
    """
    workspace = os.environ.get("GOAT_WORKSPACE", "")
    # Build project structure (pure-Python file walk; no LLM).
    try:
        result = subprocess.run(["find", workspace or ".", "-name", "*.py", "-not", "-path", "*/__pycache__/*"], capture_output=True, text=True, timeout=5)
        proj_struct = result.stdout.strip()[:2000] if result.returncode == 0 else ""
    except Exception:
        proj_struct = ""

    # Load the active behavior-style profile (formality / tone / language /
    # vocabulary / humor / length / notes) and append it to memory_context
    # so GOAT adapts every response to the user's learned style. Pure read
    # against Letta; never raises (returns empty_profile() on any failure).
    mm = getattr(registry, "memory_manager", None)
    profile_block = ""
    try:
        from supervisor.behavior.behavior_session import get_profile
        profile = await get_profile(mm)
        profile_block = _format_profile_block(profile)
    except Exception as exc:  # noqa: BLE001 — profile is enhancement, not critical
        log.debug("build_goat_context: profile load failed — %s", exc)
        profile_block = ""

    augmented_mem_ctx = mem_ctx or ""
    if profile_block:
        augmented_mem_ctx = f"{augmented_mem_ctx}\n\n{profile_block}" if augmented_mem_ctx else profile_block

    ctx = GoatContext(
        workspace=workspace,
        available_agents=_available_agents(registry),
        dag_tools=[getattr(t, "name", "") for t in getattr(registry, "file_tools", []) if getattr(t, "name", "")],
        available_tools=_available_tools(registry),
        memory_context=augmented_mem_ctx,
        has_prior_knowledge=bool(augmented_mem_ctx and len(augmented_mem_ctx) > 50),
        project_structure=proj_struct,
    )
    log.debug("build_goat_context: workspace=%s agents=%d profile=%s",
              workspace or "(unset)", len(ctx.available_agents),
              "yes" if profile_block else "no")
    return ctx
