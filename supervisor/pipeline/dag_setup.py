"""Pre-execution setup helpers for the DAG pipeline.

Pure side-effect helpers extracted from ``dag_execution.run_dag_pipeline`` so
the pipeline function stays readable and under the file-size limit:

  - ``build_plan_context`` assembles the planner context string (capabilities
    banner + history-derived context + optional lightweight hint).
  - ``persist_dag_prompt`` writes the formatted DagPrompt to
    ``dag:<session_id>:instructions`` so DAG agents can read it.
  - ``write_active_dag`` records the live DAG session id under
    ``goat:<parent_session_id>:active_dag`` so GOAT can control it.

These touch only working memory and string assembly — they do NOT alter the
DagBridge / GoatValidator verification path. No singletons; every function takes
its dependencies explicitly and degrades quietly on error.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from supervisor.classification.classifier import IntentDepth

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from supervisor.pipeline.dag_prompt_builder import DagPrompt

log = logging.getLogger("goat2.supervisor.pipeline.dag_setup")

__all__ = ["build_plan_context", "persist_dag_prompt", "write_active_dag"]

_DAG_CAPABILITIES: str = """[DAG Agent Capabilities]
tool_caller: file_read, file_write, file_create, file_list, file_search, file_grep, file_info, file_read_lines, memory_recent, memory_get, memory_store, memory_search (working tier only)
researcher: web_search, memory_search (working tier only)
coder: file_read, file_write, file_create, shell (read-only)
critic: memory_recent, memory_get (read-only)
summarizer: memory_recent (read-only)
Use tool_caller for file operations. Use researcher for web search. Use coder for code generation."""


def build_plan_context(
    supervisor, instr_intent: str, instr_ctx: str, depth: IntentDepth
) -> str:
    """Assemble the planner context string from history, capabilities, and depth."""
    plan_ctx = supervisor._history.as_plan_context(
        instr_intent, supervisor._user_profile or "", instr_ctx,
    )
    plan_ctx = f"[require_source: true]\n{_DAG_CAPABILITIES}\n{plan_ctx}"
    if depth == IntentDepth.ANALYTICAL:
        plan_ctx = f"[Lightweight: ≤2 tasks]\n{plan_ctx}"
    return plan_ctx


async def persist_dag_prompt(
    mm: "MemoryManager | None", session_id: str, dag_prompt: "DagPrompt"
) -> None:
    """Write the formatted DagPrompt to dag:<session_id>:instructions for DAG agents."""
    if not mm:
        return
    try:
        import dataclasses as _dc
        import json as _j
        import time as _t
        from config.limits import DAG_RESULT_TTL
        from config.roles import SESSION_ROLE
        from memory.working.working_record import RecordDict
        key = f"dag:{session_id}:instructions"
        now = _t.time()
        record: RecordDict = {
            "id": key, "agent_role": SESSION_ROLE, "key": key,
            "content": _j.dumps(_dc.asdict(dag_prompt), ensure_ascii=False),
            "metadata": {"type": "dag_prompt", "session_id": session_id},
            "created_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now)),
            "created_at_ts": now, "expires_at": now + DAG_RESULT_TTL,
        }
        await mm.working.backend.set(SESSION_ROLE, key, record, expires_at=record["expires_at"])
        log.debug("persist_dag_prompt: written task_id=%s agents=%s",
                  dag_prompt.task_id, dag_prompt.required_agents)
    except Exception as e:
        log.warning("persist_dag_prompt failed (non-critical): %s", e)


async def write_active_dag(
    mm: "MemoryManager | None", parent_session_id: str, dag_session_id: str
) -> None:
    """Record the live DAG session id under goat:<parent_session_id>:active_dag."""
    if not mm:
        return
    try:
        import time as _t
        from config.roles import SESSION_ROLE
        from memory.working.working_record import RecordDict
        key = f"goat:{parent_session_id}:active_dag"
        now = _t.time()
        record: RecordDict = {
            "id": key, "agent_role": SESSION_ROLE, "key": key,
            "content": dag_session_id, "metadata": {"type": "active_dag"},
            "created_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now)),
            "created_at_ts": now, "expires_at": now + 3600,
        }
        await mm.working.backend.set(SESSION_ROLE, key, record, expires_at=now + 3600)
    except Exception as e:
        log.debug("write_active_dag failed: %s", e)
