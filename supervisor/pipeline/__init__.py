"""supervisor.pipeline — the single GOAT LLM call + its context.

Two modules:
  - ``goat_enrichment`` — assemble GoatContext (no LLM, pure)
  - ``goat_call``       — the ONE LLM call per turn (decide + respond)

The DAG execution layer (planner/critic/validators) is a
separable concern that lives outside this package; the new
``supervisor/`` is built around the single-call architecture
where the LLM picks a DAG via the ``start_dag`` tool and the
DAG runs in the background without blocking the kernel.
"""
from __future__ import annotations

from supervisor.pipeline import goat_call, goat_enrichment

__all__ = ["goat_call", "goat_enrichment"]
