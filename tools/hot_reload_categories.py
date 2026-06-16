"""Per-category discovery and package-reload helpers for ``tools/hot_reload.py``.

Two reload strategies are supported by the watcher:

- **package** (this module) — re-runs a package ``__init__.py``
  via ``importlib.reload`` and re-discovers the
  ``ToolDefinition`` exports on the reloaded module. Used for
  every ``tools/<name>/`` subdirectory.
- **file** (``tools/hot_reload_discovery``) — adds / removes /
  reloads individual ``.py`` files inside a flat directory. Used
  for the external ``dynamic_tools/`` root.

Lives in its own module to keep ``hot_reload.py`` under the
260-line ceiling. The category → registry-slot mapping is a
single rule (``<dirname>_tools``) so adding a new tool package
is a zero-code change for the watcher.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import TYPE_CHECKING

from tools.hot_reload_discovery import discover_tools

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.tools.hot_reload.categories")

__all__ = [
    "STATIC_TOOLS_ROOT",
    "EXCLUDED_PACKAGE_DIRS",
    "discover_static_categories",
    "derive_slot_name",
    "reload_package_category",
]

# Absolute path of the ``tools/`` package directory. Resolved at
# import time from this file's location so it works regardless of
# the caller's CWD.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_TOOLS_ROOT: str = _THIS_DIR

# Subdirectories of ``tools/`` that the watcher must never try to
# reload — these are the watcher / discovery / categories modules
# themselves. (The spec excludes ``tools/hot_reload.py`` and
# ``__pycache__``; this set is the directory-level analogue.)
EXCLUDED_PACKAGE_DIRS: frozenset[str] = frozenset({
    "hot_reload",
    "hot_reload_discovery",
    "hot_reload_categories",
})


def derive_slot_name(dirname: str) -> str:
    """Registry-slot name for a tools subdirectory.

    Uniform rule: ``<dirname>_tools``. Works for every category:
    ``memory`` → ``memory_tools``, ``system`` → ``system_tools``,
    ``dynamic_tools`` (the external root) → ``dynamic_tools``.
    """
    return f"{dirname}_tools"


def discover_static_categories(
    tools_root: str = STATIC_TOOLS_ROOT,
) -> "list[tuple[str, str]]":
    """Return ``[(absolute_dir, registry_slot_name), ...]`` for every Python package under ``tools_root``.

    A category is a subdirectory that contains ``__init__.py`` and
    is not in ``EXCLUDED_PACKAGE_DIRS``. Returns ``[]`` when the
    root is missing or unreadable so the watcher can fall back to
    a no-op.
    """
    if not tools_root or not os.path.isdir(tools_root):
        return []
    out: list[tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(tools_root))
    except OSError as exc:
        log.warning("discover_static_categories: listdir(%s) failed: %s", tools_root, exc)
        return []
    for name in entries:
        if name in EXCLUDED_PACKAGE_DIRS:
            continue
        if name.startswith("__") or name.startswith("."):
            continue
        full = os.path.join(tools_root, name)
        if not os.path.isdir(full):
            continue
        if not os.path.isfile(os.path.join(full, "__init__.py")):
            continue
        out.append((full, derive_slot_name(name)))
    return out


def _dir_to_module_name(
    category_dir: str, tools_root: str = STATIC_TOOLS_ROOT,
) -> str:
    """Map an absolute tools package path to its dotted module name.

    ``/abs/tools/memory`` → ``tools.memory``. The dynamic root
    (which lives outside ``tools/``) is not used by this helper
    since it is handled by the file-category path.
    """
    abs_dir = os.path.abspath(category_dir)
    abs_root = os.path.abspath(tools_root)
    try:
        rel = os.path.relpath(abs_dir, abs_root)
    except ValueError:
        rel = os.path.basename(abs_dir)
    parts = [p for p in rel.split(os.sep) if p and p != "."]
    if not parts:
        return os.path.basename(abs_dir)
    return ".".join(["tools", *parts])


def reload_package_category(
    category_dir: str, slot: str, registry: "ServiceRegistry",
) -> int:
    """Reload a static ``tools/<name>/`` package and push its tools into ``registry.<slot>``.

    Returns the number of tools discovered, or 0 on failure
    (failure is logged at WARNING; the registry's prior list is
    preserved because ``update_tools`` only mutates the list in
    place on success). Never raises.
    """
    mod_name = _dir_to_module_name(category_dir)
    try:
        pkg = importlib.import_module(mod_name)
    except Exception as exc:  # noqa: BLE001
        log.warning("reload_package_category: import(%s) failed: %s", mod_name, exc)
        return 0
    try:
        importlib.reload(pkg)
    except Exception as exc:  # noqa: BLE001
        log.warning("reload_package_category: reload(%s) failed: %s", mod_name, exc)
        return 0
    try:
        tools = discover_tools(pkg)
    except Exception as exc:  # noqa: BLE001
        log.warning("reload_package_category: discover(%s) failed: %s", mod_name, exc)
        return 0
    registry.update_tools(slot, tools)
    return len(tools)
