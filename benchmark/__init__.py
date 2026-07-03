"""benchmark — standalone benchmarking suite for GOAT 2.0.

Public API:
    BenchmarkRunner  — run datasets against a live orchestrator and collect metrics
    get_dataset      — load a built-in dataset by name
    list_datasets    — enumerate built-in dataset names
    BenchmarkMetrics — aggregated metrics over a run
    Evaluator        — score responses (exact / contains / semantic / llm_judge)

The package imports no ``orchestrator`` or ``memory`` modules at import time —
``BenchmarkRunner`` builds its services lazily on first use.
"""
from __future__ import annotations

from benchmark.datasets import get_dataset, list_datasets
from benchmark.evaluator import Evaluator
from benchmark.metrics import BenchmarkMetrics
from benchmark.runner import BenchmarkRunner

__all__ = [
    "BenchmarkRunner",
    "get_dataset",
    "list_datasets",
    "BenchmarkMetrics",
    "Evaluator",
]