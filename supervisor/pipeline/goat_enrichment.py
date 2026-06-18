"""GoatContext — pure context builder (NO LLM) of the facts GOAT
needs to decide. Part of the single-call architecture: the
middleware only assembles context; the one GOAT decision call
(``pipeline.goat_call.goat_turn``) does the reasoning.

Gathers:
  - workspace root from env
  - available agent roles + tools (dynamic from the registry)
  - the project-structure scan (pure-Python, no LLM)
  - memory context (working memory + episodic recall)
  - the raw style profile text (so the supervisor can refresh
    its in-memory cache without a second Letta read)

USAGE:
    from supervisor.pipeline.goat_enrichment import build_goat_context

    goat_ctx = await build_goat_context(registry, mem_ctx)
    user_prompt = goat_ctx.to_prompt()
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.supervisor.pipeline.goat_enrichment")

__all__ = ["GoatContext", "build_goat_context"]

# Hard upper bound on the project-structure scan (seconds).
# Pure-Python defensive ceiling; in normal use the scan finishes
# well under 1 s.
_PROJECT_SCAN_TIMEOUT_S: int = 5
# Hard cap on the rendered project-structure text (chars).
# Keeps the prompt bounded even on very large repos.
_PROJECT_SCAN_MAX_CHARS: int = 2_000


@dataclasses.dataclass
class GoatContext:
    """Pure context the GOAT LLM call sees (no judgment, no decision)."""

    workspace:            str
    available_agents:     list[str]
    available_tools:      str
    memory_context:       str
    project_structure:    str   = ""
    behavior_profile:     str   = ""
    has_prior_knowledge:  bool  = False
    dag_tools:            list[str] = dataclasses.field(default_factory=list)

    def to_prompt(self) -> str:
        """Render the context as a prompt block for the GOAT decision call."""
        lines = ["[GOAT capabilities]"]
        if self.workspace:
            lines.append(f"Workspace root (use this exact path): {self.workspace}")
        if self.available_agents:
            lines.append("DAG agent roles: " + ", ".join(self.available_agents))
        if self.dag_tools:
            lines.append(
                "DAG tool_caller tools (file operations): "
                + ", ".join(self.dag_tools)
            )
        if self.available_tools:
            lines.append(self.available_tools)
        if self.memory_context:
            lines.append(f"\n[Memory]\n{self.memory_context}")
        return "\n".join(lines)


def _available_tools(registry: "ServiceRegistry") -> str:
    """Build a human-readable list of available agent roles + tool names.

    Reads ``registry.agent_registry.roles()`` for agents and
    several ``registry.<slot>`` lists for tools. Defensive: any
    failure is logged at DEBUG and the section is omitted.
    """
    parts: list[str] = []
    try:
        roles = registry.agent_registry.roles()
        if roles:
            parts.append("Agent roles: " + ", ".join(sorted(roles)))
    except Exception as exc:  # noqa: BLE001
        log.debug("_available_tools: roles() failed: %s", exc)
    for label, attr in (
        ("file tools", "file_tools"),
        ("memory tools", "memory_tools"),
        ("dag tools", "dag_tools"),
        ("system tools", "system_tools"),
        ("goat skills", "goat_skills_tools"),
    ):
        try:
            tools = getattr(registry, attr, None) or []
            names = [getattr(t, "name", "") for t in tools if getattr(t, "name", "")]
            if names:
                parts.append(f"{label}: " + ", ".join(names))
        except Exception as exc:  # noqa: BLE001
            log.debug("_available_tools: %s failed: %s", attr, exc)
    return "\n".join(parts)


def _available_agents(registry: "ServiceRegistry") -> list[str]:
    """Sorted list of DAG agent roles from the registry."""
    try:
        return sorted(registry.agent_registry.roles())
    except Exception as exc:  # noqa: BLE001
        log.debug("_available_agents: roles() unavailable: %s", exc)
        return []


def _scan_project(root: str) -> str:
    """Pure-Python project scan: list ``*.py`` files, capped.

    Uses ``subprocess.run`` with a hard timeout. Returns ``""``
    on any failure (no ``find`` available, timeout, non-zero
    return code). The output is trimmed to a fixed char cap so
    a huge monorepo cannot blow the prompt.
    """
    if not root:
        return ""
    try:
        result = subprocess.run(
            ["find", root, "-name", "*.py", "-not", "-path", "*/__pycache__/*"],
            capture_output=True, text=True,
            timeout=_PROJECT_SCAN_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("_scan_project: subprocess failed: %s", exc)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()[:_PROJECT_SCAN_MAX_CHARS]


async def build_goat_context(
    registry: "ServiceRegistry",
    mem_ctx: str = "",
) -> GoatContext:
    """Assemble the GoatContext — pure, no LLM.

    The behavior profile is read here (Letta) and attached to
    the GoatContext as ``behavior_profile`` so the supervisor
    can refresh its in-memory cache without a second Letta
    round-trip. ``build_goat_context`` is async for exactly
    that one Letta read; everything else is sync.

    Args:
        registry: ServiceRegistry (dynamic role/tool discovery).
        mem_ctx: Pre-computed memory context string (from
            ``session.mem_inject.mem_turn``).

    Returns:
        A populated ``GoatContext``. Defensive: any Letta or
        subprocess failure is logged at DEBUG and reflected as
        an empty / partial field.
    """
    workspace = os.environ.get("GOAT_WORKSPACE", "")

    # Project scan (sync) + profile load (async) run concurrently.
    scan_task = asyncio.to_thread(_scan_project, workspace or ".")
    profile_task = _load_behavior_text(getattr(registry, "memory_manager", None))
    proj_struct, behavior_text = await asyncio.gather(scan_task, profile_task)

    ctx = GoatContext(
        workspace=workspace,
        available_agents=_available_agents(registry),
        dag_tools=[
            getattr(t, "name", "")
            for t in getattr(registry, "file_tools", [])
            if getattr(t, "name", "")
        ],
        available_tools=_available_tools(registry),
        memory_context=mem_ctx or "",
        project_structure=proj_struct or "",
        behavior_profile=behavior_text or "",
        has_prior_knowledge=bool(mem_ctx and len(mem_ctx) > 50),
    )
    log.debug(
        "build_goat_context: workspace=%s agents=%d profile=%s",
        workspace or "(unset)",
        len(ctx.available_agents),
        "yes" if behavior_text else "no",
    )
    return ctx


async def _load_behavior_text(mm) -> str:
    """Read the GOAT ``persona`` block; return raw ``key: value`` text.

    Defensive: any exception → ``""``.
    """
    if mm is None:
        return ""
    try:
        from supervisor.behavior.store import load_style
        return await load_style(mm) or ""
    except Exception as exc:  # noqa: BLE001
        log.debug("_load_behavior_text failed: %s", exc)
        return ""
