"""Hot-reload watcher for the entire GOAT tools surface.

``ToolsWatcher`` polls every Python package under ``tools/``
(memory, file, goat_skills, dag, system, …) and the external
``dynamic_tools/`` root. On every change it reloads the affected
category and pushes the new ``ToolDefinition`` exports into the
matching registry slot — ``memory_tools``, ``file_tools``,
``goat_skills_tools``, ``dag_tools``, ``system_tools``,
``dynamic_tools``, or any future ``tools/<name>/`` package whose
slot follows the ``<dirname>_tools`` convention.

NO-OP WHEN DISABLED:
    ``start()`` is a silent no-op when no categories are
    discoverable (no Python packages under ``tools/`` and no
    external ``dynamic_tools/`` root). This is the safe default
    for production deployments.

MAPPING RULE:
    The category → registry-slot mapping is a single uniform
    rule: ``<dirname>_tools``. There are no per-category branches
    here — adding a new ``tools/<name>/`` package is a zero-code
    change for the watcher. The registry's ``update_tools``
    method performs the in-place mutation that keeps captured
    references valid.

DISCOVERY + SCAN LAYOUT:
    - Static categories (every ``tools/<name>/`` package) use the
      ``package`` reload strategy — ``importlib.reload`` on the
      package. Helpers live in ``tools.hot_reload_categories``.
    - The external ``dynamic_tools/`` root uses the ``file``
      strategy — add/modify/remove per ``.py`` file. Helpers live
      in ``tools.hot_reload_discovery`` (next to the original
      tool-discovery helpers to keep them together).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tools.hot_reload_categories import (
    discover_static_categories,
    reload_package_category,
)
from tools.hot_reload_discovery import (
    prime_category,
    resolve_tools_root,
    scan_file_category,
)

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.tools.hot_reload")

__all__ = ["ToolsWatcher", "_POLL_INTERVAL_S"]

# Polling cadence. 30s balances responsiveness against CPU cost.
_POLL_INTERVAL_S: float = 30.0

# Category kinds. "package" reloads via importlib.reload; "file"
# diffs the .py files inside the category directory.
_KIND_PACKAGE: str = "package"
_KIND_FILE: str = "file"


@dataclass
class _Category:
    """One watched directory and the registry slot it updates.

    ``tracked`` is a per-category state dict: for ``package`` it
    holds ``{absolute_path: mtime}`` (mtime-only — the reload
    itself is whole-package via importlib). For ``file`` it holds
    ``{absolute_path: (mtime, module_name, [tool_name, ...])}``
    so a single file's tools can be unloaded on edit / remove.
    """
    directory: str
    slot: str
    kind: str
    tracked: dict = field(default_factory=dict)


class ToolsWatcher:
    """Poll every ``tools/<name>/`` package + the external ``dynamic_tools/`` root.

    Lifecycle::

        watcher = ToolsWatcher()
        await watcher.start(registry)
        # ... watches in background ...
        await watcher.stop()

    Public surface is small and side-effect free: every method is
    idempotent, ``start()`` after ``stop()`` is supported, and the
    watch loop never raises into the asyncio task.
    """

    __slots__ = ("_registry", "_categories", "_task", "_stopped")

    def __init__(self) -> None:
        self._registry: "ServiceRegistry | None" = None
        self._categories: list[_Category] = []
        self._task: asyncio.Task | None = None
        self._stopped: bool = False

    async def start(
        self,
        registry: "ServiceRegistry",
        tools_root: str = "",
    ) -> None:
        """Start the polling task. No-op if no categories are discoverable.

        Discovers every Python package under the ``tools/`` root and
        (when present) appends the external ``dynamic_tools/``
        directory resolved from ``tools_root`` / ``GOAT_WORKSPACE``
        / ``~/.goat2/dynamic_tools``. Each category's per-file
        mtimes are primed so the first poll does not fire a reload.
        """
        if self._task is not None and not self._task.done():
            log.debug("ToolsWatcher: already running — start() is a no-op")
            return
        self._registry = registry
        self._stopped = False
        self._categories = self._discover_categories(tools_root)
        if not self._categories:
            log.debug("ToolsWatcher: disabled (no categories discoverable)")
            return
        # Prime mtimes so the first poll does not fire a reload.
        for cat in self._categories:
            cat.tracked = prime_category(cat.directory)
        self._task = asyncio.get_event_loop().create_task(
            self._watch_loop(), name="tools_hot_reload",
        )
        log.info(
            "ToolsWatcher: started (categories=%d, interval=%.0fs)",
            len(self._categories), _POLL_INTERVAL_S,
        )
        for cat in self._categories:
            log.debug("ToolsWatcher: watching %s → %s", cat.directory, cat.slot)

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

    def _discover_categories(self, tools_root: str) -> "list[_Category]":
        """Build the per-category list at startup.

        Static categories come from ``discover_static_categories``
        (one per Python package under ``tools/`` minus the
        excluded hot-reload modules). The external dynamic
        ``tools_root`` is added as a single ``file``-kind
        category when it resolves to an existing directory.
        """
        cats: list[_Category] = []
        for directory, slot in discover_static_categories():
            cats.append(_Category(directory=directory, slot=slot, kind=_KIND_PACKAGE))
        dyn_root = resolve_tools_root(tools_root)
        if dyn_root and os.path.isdir(dyn_root):
            cats.append(_Category(directory=dyn_root, slot="dynamic_tools", kind=_KIND_FILE))
        return cats

    async def _watch_loop(self) -> None:
        """Poll each category on a fixed interval. Never raises into the task."""
        try:
            while not self._stopped:
                await asyncio.sleep(_POLL_INTERVAL_S)
                if self._stopped:
                    break
                try:
                    self._scan_all()
                except Exception as exc:  # noqa: BLE001
                    log.warning("ToolsWatcher: scan_all failed: %s", exc)
        except asyncio.CancelledError:
            log.debug("ToolsWatcher: watch loop cancelled")
        except Exception as exc:  # noqa: BLE001
            log.warning("ToolsWatcher: watch loop crashed: %s", exc)

    def _scan_all(self) -> None:
        """Iterate every category, scan it for changes, and reload on transition.

        A single bad category never blocks the others: each
        category is wrapped in its own try/except. ``file``
        categories use the per-file diff from
        ``hot_reload_discovery.scan_file_category``;
        ``package`` categories reload the whole package on any
        file change inside the directory.
        """
        if self._registry is None:
            return
        for cat in self._categories:
            try:
                if cat.kind == _KIND_FILE:
                    scan_file_category(cat.directory, cat.slot, self._registry, cat.tracked)
                else:
                    self._scan_package_category(cat)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "ToolsWatcher: scan failed for %s (%s): %s",
                    cat.slot, cat.directory, exc,
                )

    def _scan_package_category(self, cat: _Category) -> None:
        """Reload a ``package``-kind category when any file under it changed.

        The tracked dict holds ``{absolute_path: mtime}`` so the
        per-poll diff is a single ``os.listdir`` + mtime
        comparison. When a change is detected, the whole package
        is reloaded (one ``importlib.reload`` per change) — this
        is the standard idiom for reloading a package and keeps
        the watcher's per-file state cheap.
        """
        if self._registry is None:
            return
        current = prime_category(cat.directory)
        if current == cat.tracked:
            return  # No file added, removed, or modified.
        # Update the mtime set to the latest snapshot before the
        # reload so a follow-up exception does not leave stale
        # state pointing at changed files.
        cat.tracked = current
        count = reload_package_category(cat.directory, cat.slot, self._registry)
        log.info(
            "ToolsWatcher: reloaded %s — %d tool(s) now in registry",
            cat.slot, count,
        )
