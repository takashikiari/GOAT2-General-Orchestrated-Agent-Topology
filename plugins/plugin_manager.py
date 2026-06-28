"""plugins.plugin_manager — registry-owned hot-reload orchestrator tool plugins.

Each ``*.py`` in ``plugins_dir`` (except ``__init__.py``) exposes
``build(registry) -> list[ToolDefinition]``. ``scan()`` reconciles the directory
(via ``plugins._loader.reconcile``), calls each plugin's ``build``, and
atomically swaps the tool list (never mutated in place) so a turn in flight
always sees a consistent set. A plugin that fails to build is skipped and its
last-known-good tools are kept — a broken edit never wipes a working tool.
Registry-owned (lazy), not a module singleton.
"""
from __future__ import annotations

from pathlib import Path
from types import ModuleType

from orchestrator.tools import ToolDefinition
from plugins._loader import reconcile
from utils.logging.setup import get_logger

log = get_logger(__name__)
__all__ = ["PluginManager"]


class PluginManager:
    """Discovers, loads, and hot-reloads orchestrator tool plugins."""

    def __init__(self, registry, plugins_dir: Path) -> None:
        self._registry = registry
        self._dir = Path(plugins_dir)
        self._mtimes: dict[str, int] = {}
        self._modules: dict[str, ModuleType] = {}
        self._good_tools: dict[str, list[ToolDefinition]] = {}
        self._tools: list[ToolDefinition] = []

    @property
    def tools(self) -> list[ToolDefinition]:
        """Current live plugin tools (caller snapshots the reference)."""
        return self._tools

    def scan(self) -> None:
        """Reconcile the plugin directory, then atomically swap the tool list."""
        reconcile(self._dir, self._modules, self._mtimes)
        for name in list(self._good_tools):
            if name not in self._modules:
                self._good_tools.pop(name, None)
        new_tools: list[ToolDefinition] = []
        for name, module in self._modules.items():
            build = getattr(module, "build", None)
            if not callable(build):
                log.warning("plugin %s has no build(); skipping", name)
                self._good_tools.pop(name, None)
                continue
            try:
                result = build(self._registry)
                if (not isinstance(result, list)
                        or not all(isinstance(t, ToolDefinition) for t in result)):
                    raise TypeError("build() must return list[ToolDefinition]")
                self._good_tools[name] = result
            except Exception as exc:  # noqa: BLE001
                log.warning("plugin build failed %s: %s, using last good", name, exc)
                result = self._good_tools.get(name, [])
            new_tools.extend(result)
        self._tools = new_tools
        log.debug("plugin scan: %d tools from %d modules", len(new_tools), len(self._modules))