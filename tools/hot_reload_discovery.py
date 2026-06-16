"""Tool discovery + path-resolution helpers for ``tools/hot_reload.py``.

Three concerns live here so the watcher module stays focused on the
polling loop:

- **Discovery** — ``discover_tools`` walks a module's top-level
  attributes and returns every ``ToolDefinition`` it finds. Two
  patterns are supported: bare module-level ToolDefinitions, and
  lists named ``*_TOOLS`` / ``*TOOLS``.
- **Path resolution** — ``resolve_tools_root`` honors an explicit
  arg > ``GOAT_WORKSPACE`` env > ``~/.goat2/dynamic_tools/``
  fallback. Used to locate the external ``dynamic_tools/`` root.
- **File-category scan/load/unload** — the file-by-file diff
  machinery the watcher uses for the external dynamic-tools
  directory. ``prime_category`` snapshots mtimes on startup;
  ``scan_file_category`` is the per-poll diff; ``load_file`` and
  ``unload_file`` perform the actual add/remove against the
  registry. Lives here so ``hot_reload.py`` and
  ``hot_reload_categories.py`` can both import it without
  splitting the state-transition code across two files.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.tools.hot_reload.discovery")

__all__ = [
    "discover_tools",
    "is_tool_definition",
    "resolve_tools_root",
    "prime_category",
    "scan_file_category",
    "load_file",
    "unload_file",
    "PY_SUFFIX",
    "MOD_PREFIX",
]


# Suffixes to scan.
PY_SUFFIX: str = ".py"

# Module-name prefix for dynamic tools. Using a unique name per file
# (sha1 of absolute path) so reloading a file does not stomp on
# whatever is already in ``sys.modules`` under a static name.
MOD_PREFIX: str = "dynamic_tools."


def discover_tools(module) -> "list[ToolDefinition]":
    """Return every ToolDefinition instance found on ``module``."""
    out: list = []
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        try:
            value = getattr(module, attr_name)
        except Exception:
            continue
        if is_tool_definition(value):
            out.append(value)
            continue
        if attr_name.endswith("TOOLS") and isinstance(value, list):
            for item in value:
                if is_tool_definition(item):
                    out.append(item)
    return out


def is_tool_definition(obj) -> bool:
    """Return True when ``obj`` looks like a ToolDefinition (has name+handler+description)."""
    if obj is None:
        return False
    if not (hasattr(obj, "name") and hasattr(obj, "handler") and hasattr(obj, "description")):
        return False
    name = getattr(obj, "name", None)
    handler = getattr(obj, "handler", None)
    if not isinstance(name, str) or not name:
        return False
    if not callable(handler):
        return False
    return True


def resolve_tools_root(supplied: str = "") -> str:
    """Resolve the watch directory: explicit arg > GOAT_WORKSPACE > ~/.goat2/dynamic_tools.

    Always returns a string (never raises). When the caller passes an
    explicit ``supplied`` arg, that wins. Otherwise the ``GOAT_WORKSPACE``
    environment variable is honored (under a ``dynamic_tools`` subdir).
    Last-resort fallback: ``~/.goat2/dynamic_tools/``.
    """
    if supplied:
        return supplied
    env_root = os.environ.get("GOAT_WORKSPACE", "").strip()
    if env_root:
        return os.path.join(env_root, "dynamic_tools")
    home = os.path.expanduser("~")
    return os.path.join(home, ".goat2", "dynamic_tools")


def prime_category(category_dir: str) -> "dict[str, float]":
    """Snapshot mtimes of every Python file under ``category_dir``.

    Used by the watcher on startup to record the "no change" state
    so the first poll does not trigger a reload. Returns a
    ``{absolute_path: mtime}`` dict; entries with unreadable
    mtimes are skipped.
    """
    out: dict[str, float] = {}
    if not category_dir or not os.path.isdir(category_dir):
        return out
    try:
        entries = os.listdir(category_dir)
    except OSError as exc:
        log.warning("prime_category: listdir(%s) failed: %s", category_dir, exc)
        return out
    for name in entries:
        if not name.endswith(PY_SUFFIX) or name.startswith("_") or name.startswith("."):
            continue
        full = os.path.join(category_dir, name)
        if not os.path.isfile(full):
            continue
        try:
            out[full] = os.path.getmtime(full)
        except OSError:
            continue
    return out


def scan_file_category(
    category_dir: str,
    slot: str,
    registry: "ServiceRegistry",
    tracked: "dict[str, tuple[float, str, list[str]]]",
) -> None:
    """Diff the current files in ``category_dir`` against ``tracked`` and apply changes.

    Detects added / modified / removed ``.py`` files and calls
    ``load_file`` / ``unload_file`` for each transition. Never
    raises — every step is wrapped.
    """
    try:
        entries = os.listdir(category_dir)
    except OSError as exc:
        log.warning("scan_file_category: listdir(%s) failed: %s", category_dir, exc)
        return
    current_paths: set[str] = set()
    for name in entries:
        if not name.endswith(PY_SUFFIX) or name.startswith("_") or name.startswith("."):
            continue
        full = os.path.join(category_dir, name)
        if not os.path.isfile(full):
            continue
        current_paths.add(full)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        prev = tracked.get(full)
        if prev is None:
            load_file(full, mtime, slot, registry, tracked)
        elif prev[0] != mtime:
            unload_file(full, prev, slot, registry, tracked)
            load_file(full, mtime, slot, registry, tracked)
    for stale in list(tracked.keys()):
        if stale not in current_paths:
            unload_file(stale, tracked[stale], slot, registry, tracked)
            tracked.pop(stale, None)


def load_file(
    filepath: str,
    mtime: float,
    slot: str,
    registry: "ServiceRegistry",
    tracked: "dict[str, tuple[float, str, list[str]]]",
) -> None:
    """Import a single ``.py`` file and add its tools to ``registry.<slot>``.

    Mirrors the previous ``ToolsWatcher._load_module`` semantics:
    never raises; import / discovery errors are logged at WARNING
    and the file is left untracked so a subsequent edit can retry.
    """
    mod_name = MOD_PREFIX + hashlib.sha1(filepath.encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    if spec is None or spec.loader is None:
        log.warning("load_file: could not build spec for %s", filepath)
        return
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        log.warning("load_file: import failed for %s: %s", filepath, exc)
        return
    tools = discover_tools(module)
    if not tools:
        log.debug("load_file: no tools discovered in %s", filepath)
        return
    names = [getattr(t, "name", "") for t in tools]
    registry.update_tools(slot, tools)
    tracked[filepath] = (mtime, mod_name, names)
    log.info(
        "load_file: loaded %d tool(s) from %s (%s)",
        len(tools), filepath, ", ".join(n for n in names if n),
    )


def unload_file(
    filepath: str,
    tracked_entry: tuple,
    slot: str,
    registry: "ServiceRegistry",
    tracked: "dict[str, tuple[float, str, list[str]]]",
) -> None:
    """Remove the file's tools from ``registry.<slot>`` and drop the module from ``sys.modules``.

    Goes through ``registry.update_tools`` so every state
    transition honors the in-place mutation contract: callers
    that captured the slot's list reference see the new contents
    on their next call.
    """
    _mtime, mod_name, names = tracked_entry
    current = list(getattr(registry, slot, []))
    keep = [t for t in current if getattr(t, "name", "") not in set(names)]
    removed = len(current) - len(keep)
    registry.update_tools(slot, keep)
    sys.modules.pop(mod_name, None)
    log.info("unload_file: unloaded %d tool(s) from %s", removed, filepath)
