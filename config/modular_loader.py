"""Per-module toml loader for the new modular config split.

Each consumer of a modular config file (``memory.toml`` /
``dag.toml`` / ``behavioral.toml`` / ``tools.toml``) reads ONLY its
own file via the helper here. There is no cross-module config
dependency: ``memory.shared.memory_daemon`` reads ``memory.toml``,
``supervisor.pipeline.workflow`` reads ``dag.toml``, and so on.

USAGE:
    from config.modular_loader import load_memory_config

    cfg = load_memory_config()             # {working: {...}, episodic: {...}, daemon: {...}}
    max_entries = cfg["working"]["max_entries"]   # 100
    warn        = cfg["working"]["warn_threshold"]  # 85

Each ``load_<name>_config()`` function returns a ``dict[str, Any]``
keyed by the section names declared in the toml file. When the
toml is missing or a section is absent, the section falls back to
an empty dict — callers are expected to use ``.get(key, default)``
or to consume the per-section fallback constants from
``config.fallbacks`` when the value is absent.

The loader is a single shared function with a tiny per-section
dispatch. It uses ``tomllib`` (Python ≥3.11) directly and never
caches — a missing file is an expected case during tests.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("goat2.config.modular_loader")

__all__ = [
    "load_memory_config",
    "load_dag_config",
    "load_behavioral_config",
    "load_tools_config",
]

_CONFIG_DIR: Path = Path(__file__).parent


def _load_raw(filename: str) -> dict[str, Any]:
    """Read a toml file from ``config/``; return ``{}`` on any failure.

    No exception, no caching. The function is intentionally tiny so
    the four domain-specific loaders below can be one-liners.
    """
    path = _CONFIG_DIR / filename
    if not path.exists():
        log.debug("modular_loader: %s missing — returning empty config", filename)
        return {}
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli  # type: ignore[import]
        with path.open("rb") as f:
            data = tomllib.load(f) if sys.version_info >= (3, 11) else tomli.load(f)  # type: ignore[union-attr]
        log.debug("modular_loader: %s loaded (top-level keys=%s)", filename, list(data))
        return data
    except Exception as exc:
        log.debug("modular_loader: %s parse error: %s — returning empty config", filename, exc)
        return {}


def load_memory_config() -> dict[str, dict[str, Any]]:
    """Return ``{working: {...}, episodic: {...}, daemon: {...}}`` from ``memory.toml``."""
    return _load_raw("memory.toml")


def load_dag_config() -> dict[str, dict[str, Any]]:
    """Return ``{execution: {...}}`` from ``dag.toml``."""
    return _load_raw("dag.toml")


def load_behavioral_config() -> dict[str, dict[str, Any]]:
    """Return ``{learning: {...}, style: {...}}`` from ``behavioral.toml``."""
    return _load_raw("behavioral.toml")


def load_tools_config() -> dict[str, dict[str, Any]]:
    """Return ``{hot_reload: {...}, shell: {...}, web_search: {...}}`` from ``tools.toml``."""
    return _load_raw("tools.toml")
