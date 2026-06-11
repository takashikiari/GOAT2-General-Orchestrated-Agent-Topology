"""Supervisor module constants for GOAT 2.0.

DAG EXECUTION LIMITS:
==================
Constants for wave-based DAG execution. These prevent runaway
parallel execution and ensure bounded resource usage.

SYNTHESIS TEMPERATURE:
====================
Temperature for synthesis/critique agents. Lower than default
for deterministic feedback and consistent output.
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("goat2.config.supervisor")

__all__ = [
    "MAX_WAVES",
    "MAX_TASKS_PER_WAVE",
    "SYNTHESIS_TEMPERATURE",
    "DEFAULT_TIMEOUT_SECONDS",
]

# Maximum concurrent waves in DAG execution
# Limits parallel task execution depth
MAX_WAVES: Final[int] = 10

# Maximum tasks per wave (limits concurrency)
# Prevents resource exhaustion from parallel execution
MAX_TASKS_PER_WAVE: Final[int] = 5

# Temperature for synthesis and critique agents
# Lower than default for deterministic feedback
SYNTHESIS_TEMPERATURE: Final[float] = 0.3

# Default timeout for DAG task execution (seconds)
DEFAULT_TIMEOUT_SECONDS: Final[int] = 300