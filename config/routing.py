"""Central routing for cross-module types and tool lists.

GOAT 2.0 — Dependency Management (routing + TYPE_CHECKING + Registry)
=====================================================================

GOAT 2.0 is split into three layers that must not import each other at
module level:

  * agents/      — concrete agent subclasses
  * supervisor/  — orchestration engine
  * tools/       — tool definitions

A naive cross-module import risks a circular chain. To prevent this:

  1. Cross-module types (AgentResult, AgentTask, ToolDefinition, Registry)
     are imported under ``if TYPE_CHECKING:`` in agents/ and re-exported
     via ``config.routing`` when a runtime value is needed.
  2. ``config/routing.py`` (this module) centralises every lazy accessor.
     Each ``get_*`` performs the import inside the function body so the
     dependency only resolves when called — never at module-import time.
  3. The single DI container lives in ``config/registry.py``
     (``ServiceRegistry``) — no other module-level singletons exist.

TYPICAL USAGE:
==============

    from config.routing import get_supervisor_result, get_dag_memory_tools

    SupervisorResult = get_supervisor_result()        # lazy: imports supervisor.types
    dag_mem_tools   = get_dag_memory_tools()         # lazy: imports tools

[debug] SECTION SUPPORT:
========================

Routing-level tracing can be toggled in two ways (env var wins):

  * ``GOAT_ROUTING_DEBUG=1`` environment variable
  * ``[debug] routing = true`` in ``config/goat.toml``

When enabled, every ``get_*`` call additionally logs at INFO level the
fully-qualified name of the resolved object. Otherwise logs stay at DEBUG.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

log = logging.getLogger("goat2.routing")

__all__ = [
    "routing_debug_enabled",
    "get_supervisor_result",
    "get_agent_result",
    "get_agent_task",
    "get_file_tools",
    "get_memory_tools",
    "get_dag_memory_tools",
]


def _emit(resolved: Any, name: str) -> None:
    """Log a routing access at INFO when debug is enabled, otherwise DEBUG.

    Args:
        resolved: The object that was just resolved.
        name:     Human-readable accessor name (e.g. "get_file_tools").
    """
    fqn = f"{type(resolved).__module__}.{type(resolved).__name__}"
    if routing_debug_enabled():
        log.info("routing: %s resolved -> %s", name, fqn)
    else:
        log.debug("routing: %s resolved -> %s", name, fqn)


def routing_debug_enabled() -> bool:
    """Return True when routing-level tracing is enabled.

    Resolution order:
      1. ``GOAT_ROUTING_DEBUG=1`` environment variable (highest priority).
      2. ``[debug] routing = true`` in ``config/goat.toml`` (tolerated).

    Returns:
        bool: True when verbose routing logs should be emitted.
    """
    if os.environ.get("GOAT_ROUTING_DEBUG") == "1":
        return True
    try:
        from config.toml_loader import load_toml
        cfg = load_toml()
        # Tolerate missing key / missing section: default to False.
        # TomlConfig exposes typed accessors only, so we read the raw
        # backing dict via the loader's module-level _load_raw() (no
        # public API exists for an arbitrary section). This keeps the
        # check side-effect-free when the key is absent.
        import config.toml_loader as _loader
        raw = _loader._load_raw()  # noqa: SLF001 — internal but stable
        return bool(raw.get("debug", {}).get("routing", False))
    except Exception:
        return False


def get_supervisor_result() -> type:
    """Lazy accessor: return the ``SupervisorResult`` class from ``supervisor.types``.

    The import is performed inside the function so this module remains
    safe to import from any context, including ``agents/`` files.

    Returns:
        type: The ``SupervisorResult`` dataclass.
    """
    log.debug("routing: get_supervisor_result requested")
    from supervisor.types import SupervisorResult
    _emit(SupervisorResult, "get_supervisor_result")
    return SupervisorResult


def get_agent_result() -> type:
    """Lazy accessor: return the ``AgentResult`` dataclass.

    ``config.agent_types`` is a leaf module with no transitive supervisor
    import, so this call is cheap and safe.

    Returns:
        type: The ``AgentResult`` dataclass.
    """
    log.debug("routing: get_agent_result requested")
    from config.agent_types import AgentResult
    _emit(AgentResult, "get_agent_result")
    return AgentResult


def get_agent_task() -> type:
    """Lazy accessor: return the ``AgentTask`` dataclass.

    Returns:
        type: The ``AgentTask`` dataclass.
    """
    log.debug("routing: get_agent_task requested")
    from config.agent_types import AgentTask
    _emit(AgentTask, "get_agent_task")
    return AgentTask


def get_file_tools() -> list:
    """Lazy accessor: return the ``FILE_TOOLS`` convenience group.

    This list aggregates file + web + shell tools. Reaches ``tools/__init__``
    which transitively imports ``agents.base_agent.ToolDefinition`` — a
    cycle that ``agents/`` files must avoid. Use this routing accessor
    instead of a direct ``from tools import FILE_TOOLS``.

    Returns:
        list[ToolDefinition]: Convenience group of file tools.
    """
    log.debug("routing: get_file_tools requested")
    from tools import FILE_TOOLS
    _emit(FILE_TOOLS, "get_file_tools")
    return FILE_TOOLS


def get_memory_tools() -> list:
    """Lazy accessor: return the ``MEMORY_TOOLS`` list (GOAT full-tier).

    Returns:
        list[ToolDefinition]: 16 memory tools with cross-tier access.
    """
    log.debug("routing: get_memory_tools requested")
    from tools import MEMORY_TOOLS
    _emit(MEMORY_TOOLS, "get_memory_tools")
    return MEMORY_TOOLS


def get_dag_memory_tools() -> list:
    """Lazy accessor: return the ``DAG_MEMORY_TOOLS`` list (DAG working-tier).

    Returns:
        list[ToolDefinition]: 4 working-tier memory tools (no ``tier`` arg).
    """
    log.debug("routing: get_dag_memory_tools requested")
    from tools import DAG_MEMORY_TOOLS
    _emit(DAG_MEMORY_TOOLS, "get_dag_memory_tools")
    return DAG_MEMORY_TOOLS
