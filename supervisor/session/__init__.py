"""supervisor.session — conversation state and memory plumbing.

Three modules, each a single responsibility:

  - ``history``           — in-memory rolling buffer (no I/O)
  - ``mem_inject``        — render the working-memory context block
  - ``turn_persistence``  — store turn + trigger style learning

The cross-tier memory fan-out itself lives in
``MemoryManager.recall``; this package orchestrates the call
and renders the result through the ``mechanisms/`` primitives.
"""
from __future__ import annotations

from supervisor.session import history, mem_inject, turn_persistence

__all__ = ["history", "mem_inject", "turn_persistence"]
