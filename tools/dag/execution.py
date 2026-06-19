"""DAG execution orchestration — the full ANALYTICAL/COMPLEX pipeline.

Extracted from GoatSupervisor to keep the supervisor class focused on
session/routing. This module owns the full DAG-execution flow:

  1. Decompose intent into a Plan (planner LLM).
  2. Run the plan through the WorkflowGraph (multi-agent DAG).
  3. Validate the result via DagBridge + GoatValidator.
  4. Critique (critic) and synthesize (summarizer) the validated output.
  5. Audit + return SupervisorResult.

The function takes the supervisor's `self` to access state, but it is
intentionally a free function so the pipeline can be unit-tested in
isolation and the supervisor class stays small.

Canonical home: ``tools.dag.execution``. The legacy import path
``supervisor.pipeline.dag_execution`` re-exports ``run_dag_pipeline``
from this module for backward compatibility.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

from supervisor.classification.classifier import IntentDepth

if TYPE_CHECKING:
    from supervisor.types import SupervisorResult

log = logging.getLogger("goat2.tools.dag.execution")

__all__ = ["run_dag_pipeline"]

_MAX_CRITIC_RETRIES: int = 2


async def run_dag_pipeline(
    supervisor,
    intent: str,
    t0: float,
    mem_ctx: str,
    dag_instructions: str = "",
) -> "SupervisorResult":
    """Execute the DAG pipeline from GOAT's self-contained instructions.

    Single-call architecture: GOAT's one decision call already produced
    ``dag_instructions``. This function FORMATS them into a DagPrompt (pure, no
    LLM) and runs the planner plus the specialized DAG agents. The
    DagBridge/GoatValidator verification path is unchanged.

    Args:
        supervisor: The GoatSupervisor instance (for state access).
        intent:    The user's original intent text (fallback objective).
        t0:        Monotonic start time for duration accounting.
        mem_ctx:   Pre-computed memory context string.
        dag_instructions: GOAT's self-contained planner objective.

    Returns:
        SupervisorResult with plan, results, summary, dag_verified.
    """
    from supervisor.pipeline.workflow import WorkflowGraph
    from supervisor.pipeline.critic_rerun import _rerun_failed_tasks
    from utils.logging.auditor import run_auditor
    from supervisor.pipeline.task_prep import prepare_tasks

    # GOAT's dag_instructions are the planner objective. Prefer the structured
    # instructions GOAT persisted (if present); else fall back to the passed text.
    instr_intent, instr_ctx = dag_instructions or intent, mem_ctx
    if supervisor.memory_manager:
        try:
            from supervisor.session.session import retrieve_dag_instructions
            raw = await retrieve_dag_instructions(
                supervisor.memory_manager, supervisor._session_id,
            )
            if raw:
                instr = json.loads(raw)
                instr_intent = instr.get("intent", instr_intent)
                instr_ctx = instr.get("context", mem_ctx)
                log.debug("dag_execution: using instructions from working memory session=%s",
                          supervisor._session_id)
        except Exception as e:
            log.debug("dag_execution: instructions read failed, using raw intent: %s", e)

    # FORMAT GOAT's instructions into a DagPrompt — pure, no LLM. The planner (a
    # specialized DAG agent) decomposes the objective and selects its own agents.
    from supervisor.pipeline.dag_setup import (
        build_plan_context, persist_dag_prompt, write_active_dag,
    )
    from supervisor.pipeline.dag_prompt_builder import build_dag_prompt
    dag_prompt = build_dag_prompt(instr_intent)
    await persist_dag_prompt(supervisor.memory_manager, supervisor._session_id, dag_prompt)

    plan_ctx = build_plan_context(supervisor, instr_intent, instr_ctx, IntentDepth.COMPLEX)

    # Lazy imports keep the supervisor/agents/ boundary clean.
    from agents.planner_decompose import decompose_plan
    plan = await decompose_plan(
        dag_prompt.technical_prompt or plan_ctx,
        supervisor.registry,
        required_agents=dag_prompt.required_agents or None,
    )
    lang = await prepare_tasks(plan.tasks, supervisor.memory_manager, intent, supervisor.registry)
    session_id = str(uuid.uuid4())
    # Write active DAG session_id to working memory so GOAT can control it.
    await write_active_dag(supervisor.memory_manager, supervisor._session_id, session_id)
    results = await WorkflowGraph(plan.tasks).execute(
        supervisor.registry, supervisor._semaphore,
        verbose=supervisor._verbose, memory_manager=supervisor.memory_manager,
        session_id=session_id,
    )

    # Unified verification: run all checks together for coherent reporting
    verification_summary = {"corroboration": None, "tool_verifier": None, "hallucination": None}
    try:
        from tools.dag.validators import check_corroboration
        corr = await check_corroboration(results)
        verification_summary["corroboration"] = {"consistent": corr.consistent, "issues": corr.issues}
        if not corr.consistent:
            log.warning("Corroboration check failed: %s", corr.issues)
    except Exception as e:
        log.debug("corroboration check skipped: %s", e)

    dag_verified, dag_detail, validation_errors = False, "", []
    if supervisor.memory_manager:
        try:
            from supervisor.pipeline.dag_bridge import DagBridge
            from tools.dag.validators import validate_dag_result
            dag_result = await DagBridge(supervisor.memory_manager).wait_for_result(
                session_id, timeout=120
            )
            if dag_result:
                dag_detail = dag_result
                report = await validate_dag_result(dag_detail, results)
                if report.passed:
                    dag_verified = True
                    log.info("GoatValidator: passed — dag_verified=True session=%s", session_id)
                else:
                    validation_errors = report.errors
                    log.warning("GoatValidator: failed — %s", report.errors)
            else:
                log.warning("DagBridge: timeout session=%s — dag_verified=False", session_id)
        except Exception as e:
            log.warning("DagBridge/GoatValidator failed: %s", e)

    # Level 1 — Tool Execution Verifier: checks tool calls against verification_criteria.
    # Runs after GoatValidator (which is kept intact). Non-critical — never blocks pipeline.
    if dag_verified and dag_prompt and dag_prompt.verification_criteria:
        try:
            from tools.dag.validators import run_tool_verifier
            verifier_report = await run_tool_verifier(results, dag_prompt)
            if not verifier_report.passed:
                log.warning(
                    "ToolVerifier: %d unmet criteria — score=%.2f unmet=%s",
                    len(verifier_report.unmet_criteria), verifier_report.score,
                    verifier_report.unmet_criteria,
                )
        except Exception as _vfy_exc:
            log.warning("tool_verifier failed (non-critical): %s", _vfy_exc)

    # Level 2 — enrich plan_ctx with DagPrompt technical objective + criteria so the
    # critic LLM evaluates against the actual intent, not the raw planner context.
    if dag_prompt:
        _criteria_lines = "\n".join(f"- {c}" for c in dag_prompt.verification_criteria)
        plan_ctx = (
            f"{plan_ctx}\n\n[Technical Objective]\n{dag_prompt.technical_prompt}"
            + (f"\n\n[Verification Criteria]\n{_criteria_lines}" if _criteria_lines else "")
        )

    if not dag_verified:
        # DAG failed validation but may have partial results — synthesize what's available
        available = [r.output for r in results.values() if r.output and not r.error]
        if available:
            from agents.critique import synthesize_results as _synth
            try:
                summary = await _synth(plan_ctx, results, critique="", registry=supervisor.registry, lang=lang)
            except Exception:
                summary = "Am obținut rezultate parțiale dar validarea a eșuat. " + " ".join(available[:2])[:500]
        else:
            summary = "Nu am putut obține rezultate verificate pentru acest task."
        critique_str = ""
    else:
        from agents.critique import critique_results, synthesize_results
        verdict = await critique_results(plan_ctx, results, supervisor.registry, lang)
        retry_count = 0
        while verdict.severity == "CRITICAL" and retry_count < _MAX_CRITIC_RETRIES:
            retry_count += 1
            log.info("Critic fallback attempt %d/%d: severity=%s",
                     retry_count, _MAX_CRITIC_RETRIES, verdict.severity)
            results = await _rerun_failed_tasks(
                plan, results, supervisor.registry, supervisor._semaphore,
                supervisor.memory_manager, session_id, verdict,
            )
            verdict = await critique_results(plan_ctx, results, supervisor.registry, lang)
        if verdict.severity == "CRITICAL":
            log.warning("Critic fallback exhausted after %d retries (severity=%s).",
                        _MAX_CRITIC_RETRIES, verdict.severity)
        elif verdict.severity == "MAJOR":
            log.info("Critic severity MAJOR — including warnings in summary, proceeding without rerun.")
        critique_str = verdict.raw
        summary = await synthesize_results(
            plan_ctx, results, critique_str, supervisor.registry,
            supervisor._user_profile or "", supervisor._behavior_style, lang,
            supervisor._history.summary, dag_detail=dag_detail if dag_verified else "",
        )
        if not summary.strip():
            tools_info = ", ".join(sorted({r.tool_name for r in results.values() if r.tool_name})) or "none"
            summary = f"Not available. Tools called: {tools_info}. No output from synthesis."

    audit = await run_auditor(results)
    sources = {tid: r.source for tid, r in results.items()}
    metadata = "; ".join(audit.anomalies) or "ok"
    total = time.monotonic() - t0
    log.info("Done in %.1fs — success=%s dag_verified=%s sources=%s",
             total, all(r.ok for r in results.values()), dag_verified, list(sources.values()))
    from supervisor.types import Plan, SupervisorResult
    # Build verification summary string for metadata_summary
    vfy_parts = []
    if verification_summary.get("corroboration"):
        c = verification_summary["corroboration"]
        vfy_parts.append(f"corroboration:{'FAIL' if not c.get('consistent') else 'PASS'}")
    if verification_summary.get("tool_verifier"):
        t = verification_summary["tool_verifier"]
        vfy_parts.append(f"tool_verifier:{t.get('score', 0):.2f}")
    vfy_str = "; ".join(vfy_parts) if vfy_parts else metadata

    r = SupervisorResult(
        intent=intent, plan=plan, results=results,
        critique=critique_str if dag_verified else "",
        summary=summary, total_duration_s=total, session_id=session_id,
        sources=sources, metadata_summary=vfy_str if vfy_parts else metadata,
        dag_verified=dag_verified, dag_detail=dag_detail,
    )
    supervisor._history.add_assistant(r.summary)
    from supervisor.session.turn_persistence import store_and_promote
    await store_and_promote(
        supervisor, len(supervisor._history.messages), intent, r.summary,
    )
    return r
