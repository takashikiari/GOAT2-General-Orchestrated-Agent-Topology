"""plugins._loader — stateless mechanics for the plugin hot-reload loader.

``module_path`` resolves a plugin file to ``(rootdir, dotted_name)`` (walking up
while parents are packages). ``reconcile`` does one scan pass: drop removed
modules, import new ones, reload changed ones (mtime-based). It mutates the
``modules``/``mtimes`` dicts the ``PluginManager`` owns — state lives there, not
here. Import/reload failures are logged and the module is left untouched so a
broken edit keeps its last-known-good code.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

from utils.logging.setup import get_logger

log = get_logger(__name__)
__all__ = ["module_path", "reconcile"]


def module_path(py_file: Path) -> tuple[Path, str]:
    """``(rootdir, dotted_name)`` for ``py_file``: walk up while parents are
    packages; the first non-package parent is the import root for ``sys.path``."""
    parts = [py_file.stem]
    parent = py_file.parent
    while (parent / "__init__.py").exists():
        parts.insert(0, parent.name)
        parent = parent.parent
    return parent, ".".join(parts)


def reconcile(plugins_dir: Path, modules: dict[str, ModuleType],
              mtimes: dict[str, int]) -> None:
    """Drop removed, import new, reload changed. Mutates ``modules``/``mtimes``."""
    current: dict[str, Path] = {}
    for p in sorted(plugins_dir.glob("*.py")):
        if p.name == "__init__.py":
            continue
        rootdir, name = module_path(p)
        if str(rootdir) not in sys.path:
            sys.path.insert(0, str(rootdir))
        current[name] = p
    for name in list(modules):
        if name not in current:
            modules.pop(name, None)
            mtimes.pop(name, None)
    for name, path in current.items():
        try:
            mtime = path.stat().st_mtime_ns
        except OSError as exc:
            log.warning("plugin stat failed %s: %s", name, exc)
            continue
        if name not in modules:
            try:
                modules[name] = importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001
                log.warning("plugin import failed %s: %s", name, exc)
                continue
        elif mtimes.get(name) != mtime:
            try:
                modules[name] = importlib.reload(modules[name])
            except Exception as exc:  # noqa: BLE001
                log.warning("plugin reload failed %s: %s, keeping last good", name, exc)
                continue
        mtimes[name] = mtime