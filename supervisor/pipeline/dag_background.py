"""Backward-compat re-export of DAG background helpers.

Canonical home: ``tools.dag.background``. New code should import
``spawn``, ``collect_finished``, ``write_completion``, or ``status``
directly from ``tools.dag.background`` (or from ``tools.dag``).
This module is kept as a thin re-export shim so existing
``from supervisor.pipeline import dag_background`` /
``from supervisor.pipeline.dag_background import spawn`` style
imports keep working unchanged.
"""
from __future__ import annotations

from tools.dag.background import (
    spawn,
    write_completion,
    collect_finished,
    status,
    _dag_runner,
)

__all__ = ["spawn", "write_completion", "collect_finished", "status"]
