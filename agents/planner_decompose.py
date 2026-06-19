"""Task decomposition engine for GOAT 2.0 — breaks intent into minimal subtasks.

REGISTRY INJECTION (PHASE 4):
=============================
decompose_plan() now requires `registry` parameter.
Uses registry.settings.supervisor.model for planner LLM calls.

BUG-020 + BUG-021 + BUG-022 fixes:
  - BUG-022: PLANNER_SYSTEM is referenced in exactly one place via
    the build_planner_request_body helper. Both ``decompose_plan``
    and ``_run_planner`` route through this helper, so a change
    to the system prompt can never drift between the two.
  - BUG-021: the user content wraps ``intent`` inside explicit
    delimiters (``<<<INTENT>>>...<<<END_INTENT>>>``) so multi-line
    intents or intents with JSON-like braces cannot break the
    prompt structure.
  - BUG-020: plan validation errors are logged at WARNING (not
    DEBUG) so the operator sees that the LLM produced a plan that
    didn't pass validation. The fallback path remains in place so
    the supervisor still has a plan to execute.
"""
from __future__ import annotations

import logging
from typing import Final, TYPE_CHECKING

from config.settings import Provider
from config.agent_types import AgentTask, AgentResult, Plan
from utils.llm_utils import _call_llm, _extract_json, _format_dep_context

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.agents.planner_decompose")

PLANNER_SYSTEM: Final[str] = (
    "You are a task decomposition engine for a multi-agent system. "
    "Break the user's intent into a minimal, focused set of subtasks. "
    "Return ONLY valid JSON — no markdown, no prose — matching this schema:\n"
    '{"tasks": [{"id": "snake_case_id", '
    '"role": "researcher|coder|critic|summarizer|tool_caller|memory", '
    '"prompt": "specific instructions for this agent", '
    '"depends_on": ["other_task_id"]}]}\n'
    "Rules:\n"
    "  - IDs must be unique snake_case strings\n"
    "  - Use depends_on to model data flow (downstream tasks receive upstream outputs)\n"
    "  - Always include a final summarizer task that depends on all other tasks\n"
    "  - Keep tasks atomic and role-appropriate\n"
    "  - 2-8 tasks total\n"
    "  - Decompose ONLY the current user intent. Ignore previous assistant responses.\n"
    "  - Do NOT use prior DAG results (web search, file reads) as input for new tasks.\n"
    "  - Memory tier mapping (ALWAYS use role=tool_caller with memory tools, NEVER file search):\n"
    "    * redis / working memory: memory_recent(tier=working)\n"
    "    * chromadb / episodic memory: memory_recent(tier=episodic)\n"
    "    * letta / long term memory: memory_recent(tier=long_term)\n"
    "    * memory check: use memory_recent, memory_search, memory_get tools\n"
    "  - researcher role is ONLY for external web search, never for internal memory or files\n"
    "  - tool_caller role handles file operations AND memory queries\n"
)

# Maximum characters for the user-content intent block. Tunable
# via config/agents.toml [planner] in a follow-up; 4_000 keeps
# the prompt budget bounded.
_MAX_INTENT_CHARS: Final[int] = 4_000

# Explicit delimiters wrapping the intent block. The LLM sees
# this boundary and treats the content inside as opaque user text
# — not as prompt structure or JSON.
_INTENT_OPEN:  Final[str] = "<<<INTENT>>>"
_INTENT_CLOSE: Final[str] = "<<<END_INTENT>>>"


def build_planner_request_body(
    intent: str,
    context_text: str = "",
) -> list[dict[str, str]]:
    """Compose the canonical [system, user] messages for the planner.

    BUG-022 fix: the single source of truth for the planner prompt
    structure. Both ``decompose_plan`` and ``_run_planner`` route
    through this helper so the system prompt and intent wrapping
    can never drift between the two call sites.

    BUG-021 fix: ``intent`` is wrapped inside explicit delimiters so
    newlines, JSON braces, or other special characters in the
    intent text cannot be confused with prompt structure by the
    LLM.

    Args:
        intent: Raw user intent (truncated to ``_MAX_INTENT_CHARS``).
        context_text: Optional prior-task output to thread into
            the user content. Empty string omits the section.

    Returns:
        A list of two message dicts: ``[system, user]``.
    """
    safe_intent = (intent or "")[:_MAX_INTENT_CHARS]
    user_parts: list[str] = []
    user_parts.append("Decompose this intent into tasks:")
    user_parts.append("")
    user_parts.append(_INTENT_OPEN)
    user_parts.append(safe_intent)
    user_parts.append(_INTENT_CLOSE)
    if context_text and context_text.strip():
        user_parts.append("")
        user_parts.append("Prior task context:")
        user_parts.append(context_text.strip())
    return [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user",   "content": "\n".join(user_parts)},
    ]


def _fallback_plan(intent: str) -> Plan:
    """Return a minimal safe fallback plan when validation fails."""
    return Plan(tasks=[
        AgentTask(
            id="tool_caller_1",
            role="tool_caller",
            prompt=intent,
            depends_on=[],
        ),
        AgentTask(
            id="summarize_1",
            role="summarizer",
            prompt=intent,
            depends_on=["tool_caller_1"],
        ),
    ])


async def decompose_plan(
    intent: str,
    registry: "Registry",
    required_agents: list[str] | None = None,
) -> Plan:
    """Call the supervisor model to decompose intent into an AgentTask DAG.

    BUG-022 fix: routes through ``build_planner_request_body`` so the
    system prompt + intent wrapping are identical to the call site
    used by ``_run_planner``.

    BUG-020 fix: when ``validate_plan`` rejects the LLM output, the
    validation errors are logged at WARNING (not DEBUG) so the
    operator sees them; the fallback plan is still returned so the
    supervisor has something to execute.

    Args:
        intent: Technical prompt (from DagPrompt) or raw plan context.
        registry: ServiceRegistry for model configuration.
        required_agents: Optional agent roles from DagPrompt used as guidance
            for task creation. Planner still decides execution order and waves.
    """
    _settings = registry.settings
    spec = _settings.supervisor.model
    log.debug("decompose_plan: spec=%s agents_hint=%s", spec, required_agents)
    user_body = build_planner_request_body(intent=intent)
    if required_agents:
        user_body[1]["content"] += (
            "\n\nSuggested agents (guidance only — not a hard constraint): "
            + ", ".join(required_agents)
        )
    raw = await _call_llm(
        spec,
        user_body,
        json_mode=(spec.provider == Provider.OPENAI),
        temperature=_settings.get_agent_temperature("planner", default=0.2),
    )
    try:
        data  = _extract_json(raw)
        tasks = [
            AgentTask(
                id=t["id"], role=t["role"], prompt=t["prompt"],
                depends_on=t.get("depends_on", []),
            )
            for t in data["tasks"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        log.warning(
            "decompose_plan: malformed LLM output (%s) — using fallback plan",
            exc,
        )
        return _fallback_plan(intent)

    # Force final summarizer to depend on all other tasks
    summarizer_tasks = [t for t in tasks if t.role == "summarizer"]
    if summarizer_tasks:
        final_sum = summarizer_tasks[-1]
        all_other_ids = [t.id for t in tasks if t.id != final_sum.id]
        final_sum.depends_on = list(set(final_sum.depends_on) | set(all_other_ids))

    plan = Plan(tasks=tasks)

    # ── Validate the plan before returning ──────────────────────────────
    # Lazy import breaks the circular chain: planner_decompose → supervisor
    from supervisor.pipeline.plan_validator import validate_plan  # noqa: PLC0415
    is_valid, errors, warnings = validate_plan(plan)

    # BUG-020 fix: log validation diagnostics at WARNING (operator-
    # visible) instead of silently choosing the fallback.
    if warnings:
        for w in warnings:
            log.warning("decompose_plan: plan warning: %s", w)
    if not is_valid:
        for err in errors:
            log.warning(
                "decompose_plan: plan validation failed: %s — falling back",
                err,
            )
        log.warning("decompose_plan: returning fallback plan (LLM output rejected)")
        return _fallback_plan(intent)

    log.debug("decompose_plan: plan validated tasks=%d", len(plan.tasks))
    return plan


async def _run_planner(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Built-in planner runner — registered for completeness; supervisor uses decompose_plan directly.

    BUG-022 fix: routes through ``build_planner_request_body`` so
    the system prompt + intent wrapping are identical to those
    used by ``decompose_plan``.
    """
    _settings = registry.settings
    task.source = "generated"
    spec    = _settings.agents.get("planner")
    context = _format_dep_context(dep_results)
    log.debug("_run_planner: task_id=%s spec=%s", task.id, spec)
    body = build_planner_request_body(intent=task.prompt, context_text=context)
    return await _call_llm(
        spec,
        body,
        json_mode=True,
        temperature=_settings.get_agent_temperature("planner", default=0.2),
    )