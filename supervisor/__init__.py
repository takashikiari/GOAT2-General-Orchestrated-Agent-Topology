"""supervisor — GOAT 2.0 orchestration package.

Single-call architecture:

    middleware (no LLM)  →  ONE LLM call  →  dispatch
                                         ↓
                          (DAG runs in background, never blocks)

LAYOUT:
    supervisor.supervisor      — GoatSupervisor orchestrator
    supervisor.identity        — GOAT_SYSTEM (operational rules)
    supervisor.pipeline        — the single LLM call + its context
    supervisor.behavior        — learned communication style
    supervisor.classification  — pure-Python routing logic
    supervisor.session         — conversation state + memory plumbing
    supervisor.mechanisms      — pure-Python middleware primitives
    supervisor.interfaces      — external adapters (Telegram)

The single LLM call lives in
``supervisor.pipeline.goat_call.goat_turn``. Everything else
in the package is pure Python, dependency-injected, and
free of LLM calls.
"""
from __future__ import annotations

from supervisor.supervisor import GoatSupervisor

__all__ = ["GoatSupervisor"]
