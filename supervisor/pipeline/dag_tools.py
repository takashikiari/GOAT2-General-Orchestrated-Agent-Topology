"""Backward-compat re-export of ``make_dag_tools``.

The canonical implementation lives in ``tools/dag/__init__.py`` so the
tool surface is grouped with the other tool packages
(``tools/file``, ``tools/web``, ``tools/system``, ``tools/goat_skills``,
``tools/memory``). This module is kept as a thin re-export shim so
existing imports like ``from supervisor.pipeline.dag_tools import
make_dag_tools`` continue to work without changes elsewhere in the
codebase.

New code should import directly from ``tools.dag``.
"""
from __future__ import annotations

from tools.dag import make_dag_tools

__all__ = ["make_dag_tools"]
