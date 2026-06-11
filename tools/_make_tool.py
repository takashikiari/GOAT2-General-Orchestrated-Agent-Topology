"""Tool factory for tools/ — lazy import of ``agents.base_agent.ToolDefinition``.

ARCHITECTURE (routing + TYPE_CHECKING + Registry):
==================================================
``tools/`` MUST NOT import from ``agents/`` at module level — that would
create a tools -> agents -> tools cycle (the agent base class transitively
reaches back into tools/ for tool dispatch). To break the cycle while
still allowing the convenient module-level ``MY_TOOL = make_tool(...)``
pattern, this helper hides the cross-layer import inside a function
body. The import is performed only when ``make_tool`` is called, which
in practice means at tools/ package load time — but the import is
*function-local* and never visible at module scope, satisfying the
architectural rule.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.make_tool")

__all__ = ["make_tool"]


def make_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    handler: Any,
) -> "ToolDefinition":
    """Build a ToolDefinition with a lazy import of ``agents.base_agent``.

    The import is performed inside this helper so that tools/ files keep
    ``agents`` out of their module-level imports. This avoids any
    module-load-time coupling between the tools layer and the agent
    base class, even though the dependency is one-way and cycle-free
    at runtime.

    Args:
        name: Tool identifier (e.g. ``"file_read"``).
        description: Human-readable tool description.
        parameters: JSON-Schema-style parameter definition.
        handler: Async or sync callable invoked by the supervisor.

    Returns:
        A ``ToolDefinition`` instance.
    """
    from agents.base_agent import ToolDefinition
    log.debug("make_tool: building tool name=%r", name)
    return ToolDefinition(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
    )
