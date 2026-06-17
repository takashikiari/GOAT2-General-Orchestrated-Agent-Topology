"""Timeout constants for the DAG pipeline (asyncio-friendly floats).

Pure re-export layer over ``config.limits``. ``asyncio.wait_for`` requires
floats (not ints), so we convert at import time and avoid per-call
``float(...)`` overhead inside ``workflow.py``.

USAGE:
    from supervisor.pipeline.timeouts import WAVE_TIMEOUT_S, TASK_TIMEOUT_S
    await asyncio.wait_for(coro, timeout=TASK_TIMEOUT_S)

If you ever need to read the raw integer limits (e.g. to display in logs
or surface them via a tool), import directly from ``config.limits``.
"""
from __future__ import annotations

from config.limits import DAG_TIMEOUT, TASK_TIMEOUT, WAVE_TIMEOUT

__all__ = ["DAG_TIMEOUT_S", "WAVE_TIMEOUT_S", "TASK_TIMEOUT_S"]

DAG_TIMEOUT_S: float = float(DAG_TIMEOUT)
"""Hard upper bound on an entire DAG run, in seconds (float for asyncio)."""

WAVE_TIMEOUT_S: float = float(WAVE_TIMEOUT)
"""Hard upper bound on a single wave execution, in seconds (float for asyncio)."""

TASK_TIMEOUT_S: float = float(TASK_TIMEOUT)
"""Hard upper bound on a single task run, in seconds (float for asyncio)."""