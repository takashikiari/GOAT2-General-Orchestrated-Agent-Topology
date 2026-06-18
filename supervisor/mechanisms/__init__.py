"""supervisor.mechanisms — pure-Python middleware for the GOAT turn.

Every module in this package is:
  - Pure Python (no LLM calls, no regex).
  - Synchronous-safe to import (no I/O at import time, except
    ``freshness`` which reads a static toml file once at import
    and caches the result).
  - Free of state and singletons; each function is a small,
    testable, dependency-free primitive that the supervisor
    composes at run time.

The ``antirepeat`` module is the only exception that accepts
arbitrary objects (it must walk the LLM-supplied message list);
all others are pure functions of their arguments.
"""
from __future__ import annotations

from supervisor.mechanisms import (
    antirepeat,
    context_builder,
    corrections,
    freshness,
    hints,
    namespace,
    staleness,
    style_sync,
)

__all__ = [
    "antirepeat",
    "context_builder",
    "corrections",
    "freshness",
    "hints",
    "namespace",
    "staleness",
    "style_sync",
]
