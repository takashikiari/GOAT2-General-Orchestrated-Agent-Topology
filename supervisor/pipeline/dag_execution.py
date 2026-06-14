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
    from supervisor.pipeline.goat_enrichment import GoatDecision

log = logging.getLogger("goat2.supervisor.pipeline")

__all__ = ["run_dag_pipeline"]

_MAX_CRITIC_RETRIES: int = 2


async def run_dag_pipeline(
    supervisor,
    intent: str,
    t0: float,
    depth: IntentDepth,
    mem_ctx: str,
    decision: "GoatDecision | None" = None,
) -> "SupervisorResult":
    """Execute the full ANALYTICAL or COMPLEX DAG pipeline.

    Architecture: GOAT decides → Prompter formats → DAG executes. GOAT's
    enrichment ``decision`` is threaded in and handed to the Prompter
    (build_dag_prompt). When ``decision`` is None (e.g. the pending-DAG fast
    path), this function enriches the intent itself so the Prompter always
    receives a decision instead of a raw intent.

    Args:
        supervisor: The GoatSupervisor instance (for state access).
        intent:    The user's intent text.
        t0:        Monotonic start time for duration accounting.
        depth:     IntentDepth.ANALYTICAL or IntentDepth.COMPLEX.
        mem_ctx:   Pre-computed memory context string.
        decision:  GOAT's enrichment decision; enriched here if None.

    Returns:
        SupervisorResult with plan, results, summary, dag_verified.
    """
    from supervisor.pipeline.workflow import WorkflowGraph
    from supervisor.pipeline.critic_rerun import _rerun_failed_tasks
    from supervisor.logging.auditor import run_auditor
    from supervisor.pipeline.task_prep import prepare_tasks

    # Read structured instructions GOAT wrote before calling this function.
    # Falls back to raw intent if the key is missing (backward compat).
    instr_intent, instr_ctx = intent, mem_ctx
    if supervisor.memory_manager:
        try:
            from supervisor.session.session import retrieve_dag_instructions
            raw = await retrieve_dag_instructions(
                supervisor.memory_manager, supervisor._session_id,
            )
            if raw:
                instr = json.loads(raw)
                instr_intent = instr.get("intent", intent)
                instr_ctx = instr.get("context", mem_ctx)
                log.debug("dag_execution: using instructions from working memory session=%s",
                          supervisor._session_id)
        except Exception as e:
            log.debug("dag_execution: instructions read failed, using raw intent: %s", e)

    # GOAT decides → Prompter formats. Ensure a GoatDecision exists (enrich here
    # only when the caller did not provide one, e.g. the pending-DAG fast path).
    from supervisor.classification.classifier_prompt import format_history
    history_text = format_history(supervisor._history.messages) if supervisor._history else ""
    if decision is None:
        from supervisor.pipeline.goat_enrichment import enrich_intent
        decision = await enrich_intent(instr_intent, instr_ctx, history_text, supervisor.registry)
        log.debug("dag_execution: enriched intent in-pipeline (no decision passed)")

    # Build DagPrompt — the Prompter FORMATS GOAT's decision into a technical objective.
    # DAG receives technical_prompt instead of raw intent; required_agents guide the planner.
    from supervisor.pipeline.dag_setup import (
        build_plan_context, persist_dag_prompt, write_active_dag,
    )
    dag_prompt = None
    try:
        from supervisor.pipeline.dag_prompt_builder import build_dag_prompt
        dag_prompt = await build_dag_prompt(
            decision, instr_ctx, history_text, supervisor.registry,
        )
    except Exception as _dp_exc:
        log.warning("dag_execution: build_dag_prompt failed, using raw intent: %s", _dp_exc)
    # Persist DagPrompt to dag:<session_id>:instructions so DAG agents can read it.
    if dag_prompt is not None:
        await persist_dag_prompt(supervisor.memory_manager, supervisor._session_id, dag_prompt)

    plan_ctx = build_plan_context(supervisor, instr_intent, instr_ctx, depth)

    # Lazy imports keep the supervisor/agents/ boundary clean.
    from agents.planner_decompose import decompose_plan
    plan = await decompose_plan(
        dag_prompt.technical_prompt if dag_prompt else plan_ctx,
        supervisor.registry,
        required_agents=dag_prompt.required_agents if dag_prompt else None,
    )
    lang = await prepare_tasks(plan.tasks, supervisor.memory_manager, intent, supervisor.registry)
    session_id = str(uuid.uuid4())
    # Write active DAG session_id to working memory so GOAT can control it.
    await write_active_dag(supervisor.memory_manager, supervisor._session_id, session_id)
    results = await WorkflowGraph(plan.tasks).execute(
        supervisor.agent_registry, supervisor._semaphore,
        verbose=supervisor._verbose, memory_manager=supervisor.memory_manager,
        session_id=session_id,
    )

    # Unified verification: run all checks together for coherent reporting
    verification_summary = {"corroboration": None, "tool_verifier": None, "hallucination": None}
    try:
        from supervisor.pipeline.agent_corroboration import check_corroboration
        corr = await check_corroboration(results, supervisor.registry)
        verification_summary["corroboration"] = {"consistent": corr.consistent, "issues": corr.issues}
        if not corr.consistent:
            log.warning("Corroboration check failed: %s", corr.issues)
    except Exception as e:
        log.debug("corroboration check skipped: %s", e)

    dag_verified, dag_detail, validation_errors = False, "", []
    if supervisor.memory_manager:
        try:
            from supervisor.pipeline.dag_bridge import DagBridge
            from supervisor.pipeline.goat_validator import validate_dag_result
            dag_result = await DagBridge(supervisor.memory_manager).wait_for_result(
                session_id, timeout=120
            )
            if dag_result:
                dag_detail = dag_result
                report = await validate_dag_result(dag_detail, results, supervisor.registry)
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
            from supervisor.pipeline.tool_verifier import run_tool_verifier
            verifier_report = await run_tool_verifier(results, dag_prompt, supervisor.registry)
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
