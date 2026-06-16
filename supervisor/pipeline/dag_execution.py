"""Backward-compat re-export of ``run_dag_pipeline``.

Canonical home: ``tools.dag.execution``. New code should import
``run_dag_pipeline`` directly from ``tools.dag.execution`` (or from
``tools.dag``). This module is kept as a thin re-export shim so
existing ``from supervisor.pipeline.dag_execution import
run_dag_pipeline`` style imports keep working unchanged.
"""
from __future__ import annotations

from tools.dag.execution import run_dag_pipeline

__all__ = ["run_dag_pipeline"]
