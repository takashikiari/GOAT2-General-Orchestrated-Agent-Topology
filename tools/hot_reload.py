"""Hot-reload watcher for user-defined dynamic tools.

``ToolsWatcher`` polls a directory (default: ``GOAT_WORKSPACE`` env var,
or the workspace root) every ``_POLL_INTERVAL_S`` seconds, looking for
Python files that export one or more ``ToolDefinition`` objects. New
files are imported and their tools are appended to
``registry.dynamic_tools``. Modified files are reloaded (the previous
exports are removed, the new ones appended). Deleted files are
unloaded. Import errors never crash the watcher — they are logged at
WARNING and the file is skipped.

NO-OP WHEN DISABLED:
    If the resolved tools directory does not exist, ``start()`` is a
    silent no-op. This is the safe default for production deployments
    where dynamic tools are not wanted.

Tool discovery + path resolution live in
``tools.hot_reload_discovery`` to keep this module under the 260-line
ceiling.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import os
import sys
from typing import TYPE_CHECKING

from tools.hot_reload_discovery import (
    PY_SUFFIX, MOD_PREFIX, discover_tools, resolve_tools_root,
)

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.hot_reload")

__all__ = ["ToolsWatcher", "_POLL_INTERVAL_S"]

# Polling cadence. 30s balances responsiveness against CPU cost.
_POLL_INTERVAL_S: float = 30.0


class ToolsWatcher:
    """Poll a directory for new / modified / removed tool files.

    Lifecycle::

        watcher = ToolsWatcher()
        await watcher.start(registry, tools_root="/path/to/dynamic")
        # ... watches in background ...
        await watcher.stop()

    Public surface is small and side-effect free: every method is
    idempotent, ``start()`` after ``stop()`` is supported, and the
    watch loop never raises into the asyncio task.
    """

    __slots__ = (
        "_registry", "_tools_root", "_task",
        # Absolute path -> (mtime, module_name, [tool_name, ...])
        "_tracked", "_stopped",
    )

    def __init__(self) -> None:
        self._registry: "ServiceRegistry | None" = None
        self._tools_root: str = ""
        self._task: asyncio.Task | None = None
        self._tracked: dict[str, tuple[float, str, list[str]]] = {}
        self._stopped: bool = False

    async def start(
        self,
        registry: "ServiceRegistry",
        tools_root: str = "",
    ) -> None:
        """Start the polling task. No-op if the directory is missing or already running.

        Args:
            registry: ServiceRegistry that owns ``dynamic_tools``. The
                watcher appends/removes ``ToolDefinition`` objects from
                this list as files change.
            tools_root: Directory to watch. When empty, ``GOAT_WORKSPACE``
                or ``~/.goat2/dynamic_tools/`` is used. When the
                resolved directory does not exist, ``start()`` is a
                silent no-op.
        """
        if self._task is not None and not self._task.done():
            log.debug("ToolsWatcher: already running — start() is a no-op")
            return
        self._registry = registry
        self._tools_root = resolve_tools_root(tools_root)
        if not self._tools_root or not os.path.isdir(self._tools_root):
            log.debug(
                "ToolsWatcher: disabled (tools_root=%r not a directory)",
                self._tools_root,
            )
            return
        self._stopped = False
        # Prime the tracked set with the current directory state so
        # the first poll does not re-import everything.
        self._scan_directory(self._tools_root)
        self._task = asyncio.get_event_loop().create_task(
            self._watch_loop(), name="tools_hot_reload"
        )
        log.info(
            "ToolsWatcher: started (root=%s, tracked=%d, interval=%.0fs)",
            self._tools_root, len(self._tracked), _POLL_INTERVAL_S,
        )

    async def stop(self) -> None:
        """Stop the polling task. Idempotent — safe to call from any context."""
        self._stopped = True
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None
        log.debug("ToolsWatcher: stopped")

    async def _watch_loop(self) -> None:
        """Poll the directory on a fixed interval. Never raises into the task."""
        try:
            while not self._stopped:
                await asyncio.sleep(_POLL_INTERVAL_S)
                if self._stopped:
                    break
                try:
                    self._scan_directory(self._tools_root)
                except Exception as exc:  # noqa: BLE001
                    log.warning("ToolsWatcher: scan failed: %s", exc)
        except asyncio.CancelledError:
            log.debug("ToolsWatcher: watch loop cancelled")
        except Exception as exc:  # noqa: BLE001
            log.warning("ToolsWatcher: watch loop crashed: %s", exc)

    def _scan_directory(self, dirpath: str) -> None:
        """Diff the current directory state against the tracked set; apply changes."""
        try:
            entries = os.listdir(dirpath)
        except OSError as exc:
            log.warning("ToolsWatcher: listdir(%s) failed: %s", dirpath, exc)
            return
        current_paths: set[str] = set()
        for name in entries:
            # Convention: leading-underscore files are private.
            if not name.endswith(PY_SUFFIX) or name.startswith("_"):
                continue
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full):
                continue
            current_paths.add(full)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            tracked = self._tracked.get(full)
            if tracked is None:
                self._load_module(full, mtime)
            elif tracked[0] != mtime:
                # File changed — unload the old module first, then load.
                self._unload_module(full, tracked)
                self._load_module(full, mtime)
        # Detect removals.
        for stale in list(self._tracked.keys()):
            if stale not in current_paths:
                self._unload_module(stale, self._tracked[stale])
                self._tracked.pop(stale, None)

    def _load_module(self, filepath: str, mtime: float) -> None:
        """Import a single .py file and add its discovered tools to the registry.

        Never raises — import / discovery errors are logged at WARNING
        and the file is left untracked so a subsequent edit can retry.
        """
        if self._registry is None:
            return
        mod_name = MOD_PREFIX + hashlib.sha1(filepath.encode()).hexdigest()[:12]
        spec = importlib.util.spec_from_file_location(mod_name, filepath)
        if spec is None or spec.loader is None:
            log.warning("ToolsWatcher: could not build spec for %s", filepath)
            return
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            log.warning("ToolsWatcher: import failed for %s: %s", filepath, exc)
            return
        tools = discover_tools(module)
        if not tools:
            log.debug("ToolsWatcher: no tools discovered in %s", filepath)
            return
        # Register and track the tool names so we can clean up on unload.
        names = [getattr(t, "name", "") for t in tools]
        self._registry.dynamic_tools.extend(tools)
        self._tracked[filepath] = (mtime, mod_name, names)
        log.info(
            "ToolsWatcher: loaded %d tool(s) from %s (%s)",
            len(tools), filepath, ", ".join(n for n in names if n),
        )

    def _unload_module(self, filepath: str, tracked: tuple) -> None:
        """Remove the file's tools from the registry and drop the module from sys.modules."""
        if self._registry is None:
            return
        _mtime, mod_name, names = tracked
        keep = [t for t in self._registry.dynamic_tools
                if getattr(t, "name", "") not in set(names)]
        removed = len(self._registry.dynamic_tools) - len(keep)
        self._registry.dynamic_tools = keep
        sys.modules.pop(mod_name, None)
        log.info(
            "ToolsWatcher: unloaded %d tool(s) from %s", removed, filepath,
        )
