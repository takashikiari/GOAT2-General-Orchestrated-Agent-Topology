"""Shared helpers for the ``tools/goat_skills`` package.

Centralises optional-dependency imports so every tool module has a
single, consistent way to degrade gracefully when a third-party
library is missing.

GOAT-ONLY: this module is part of the ``goat_skills`` surface and is
imported only by direct_response() (see supervisor.identity).
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

log = logging.getLogger("goat2.tools.goat_skills.common")

__all__ = ["safe_import"]


def safe_import(module_name: str, attr: str | None = None) -> Any:
    """Import a module (or an attribute on it) without raising ImportError.

    Args:
        module_name: Dotted module path (e.g. ``"PIL.ImageGrab"``).
        attr: Optional attribute name to fetch from the imported module.
              Use ``None`` to return the module itself.

    Returns:
        The imported module, the requested attribute, or ``None`` if the
        import failed. A ``WARNING`` is logged on failure so operators
        can see which optional dependency is missing.
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        log.warning("goat_skills: optional dependency missing: %s (%s)", module_name, exc)
        return None
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("goat_skills: unexpected import error for %s: %s", module_name, exc)
        return None
    if attr is None:
        return module
    try:
        return getattr(module, attr)
    except AttributeError:
        log.warning("goat_skills: %s has no attribute %r", module_name, attr)
        return None
