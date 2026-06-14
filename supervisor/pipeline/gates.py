"""Retired — pre-execution gates folded into the single GOAT decision call.

The former ``check_intent_clarity_gate`` and ``validate_dag_prompt_gate`` each
ran their own LLM call before GOAT replied. In the single-call architecture
(``supervisor.pipeline.goat_decision.decide``) GOAT judges clarity and decides
whether to run a DAG in one step, so these gates no longer exist. This module is
intentionally empty and kept only to avoid breaking stale imports.
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.supervisor.pipeline.gates")

__all__: list[str] = []
