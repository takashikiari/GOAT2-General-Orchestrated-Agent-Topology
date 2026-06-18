"""supervisor.behavior — learned communication style for GOAT 2.0.

Four small modules, each a single responsibility:

  - ``profile``  — BehaviorProfile dataclass + serialize / deserialize
  - ``store``    — Letta read/write of the ``persona`` core-memory block
  - ``mirror``   — render a stored profile as a system-prompt directive
  - ``analyzer`` — pure-Python style scoring over recent user turns

The whole package is pure-Python middleware: no LLM, no regex,
no I/O of its own (the store module is the only place that
talks to Letta, via ``MemoryManager.get_block`` / ``set_block``).
"""
from __future__ import annotations

from supervisor.behavior import analyzer, mirror, profile, store

__all__ = ["analyzer", "mirror", "profile", "store"]
