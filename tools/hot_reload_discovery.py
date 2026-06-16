"""Tool discovery + path-resolution helpers for ``tools/hot_reload.py``.

Two discovery patterns are supported so authors can use whichever is
more natural:

- Module-level attributes whose name ends with ``_TOOLS`` or ``TOOLS``
  and whose value is a list of ``ToolDefinition`` instances. (e.g.
  ``MY_TOOLS = [tool1, tool2]``)
- Any top-level attribute that is itself a ``ToolDefinition``
  instance. (e.g. ``GREETER = make_tool(name='greet', ...)``)

Lives in its own module to keep ``hot_reload.py`` under the 260-line
ceiling. Also exports the watcher's module-naming and resolution
constants so the main module stays focused on the polling loop.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.hot_reload.discovery")

__all__ = [
    "discover_tools",
    "is_tool_definition",
    "resolve_tools_root",
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

