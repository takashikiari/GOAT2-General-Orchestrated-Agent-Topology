"""DagPrompt builder — the Prompter that FORMATS GOAT's decision for the DAG.

Architecture contract: **GOAT decides → Prompter formats → DAG executes.**

GOAT (``goat_enrichment.enrich_intent``) has already decided WHAT to execute and
produced a ``GoatDecision``. ``build_dag_prompt()`` consumes that decision and a
single LLM call shapes it into a structured ``DagPrompt``. The Prompter never
re-decides the intent and never invents work.

Everything the LLM needs is supplied as context at call time — the enriched
instruction, tool hints, workspace context, constraints, and the agent roles
that are actually registered (discovered dynamically from the registry). There
are NO hardcoded role lists, workspace paths, verification-criteria templates, or
decision rules in this module: the only fixed strings are the JSON schema keys.
``validate_dag_prompt()`` then confirms the result is non-empty and specific
enough for autonomous execution (an LLM judgment, not a rule table).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from typing import TYPE_CHECKING

from config.settings import Provider
from utils.llm_utils import _call_llm, _extract_json, _extract_balanced_json

if TYPE_CHECKING:
    from config.registry import Registry
    from supervisor.pipeline.intent_clarity import ClarityResult
    from supervisor.pipeline.goat_enrichment import GoatDecision

log = logging.getLogger("goat2.supervisor.pipeline.dag_prompt_builder")

__all__ = ["DagPrompt", "build_dag_prompt", "validate_dag_prompt"]

_SYSTEM: str = (
    "You are GOAT's DAG Prompter. GOAT has ALREADY DECIDED what to execute and given you an "
    "enriched instruction plus tool hints, workspace context, and constraints. Your only job "
    "is to FORMAT that decision into a structured DAG execution plan as JSON. Do NOT re-decide "
    "the intent and do NOT invent work. Reason only from the decision context in the user "
    "message.\n\n"
    "Return ONLY this JSON object — no prose, no markdown — with exactly these keys:\n"
    "{\n"
    '  "technical_prompt": "",\n'
    '  "required_agents": [],\n'
    '  "verification_criteria": [],\n'
    '  "memory_updates": true,\n'
    '  "constraints": {}\n'
    "}\n\n"
    "Fill each field by reasoning from the provided context:\n"
    "  - technical_prompt: a comprehensive, self-contained objective for the DAG planner. "
    "Preserve EVERY detail from the enriched instruction (targets, paths, scope, limits). "
    "Describe the work to be done, not how to control the DAG, and do not reference this JSON.\n"
    "  - required_agents: choose ONLY from the available agent roles listed in the user "
    "message; let the tool hints guide which you pick. Never invent role names.\n"
    "  - verification_criteria: observable outcomes that would prove THIS specific task "
    "succeeded, derived from the enriched instruction — not generic placeholders.\n"
    "  - memory_updates: true when agents should write findings to working memory.\n"
    "  - constraints: carry over and refine the decision's constraints and workspace context "
    "that the planner must respect.\n"
    "Respond in the same language as the enriched instruction."
)

_VALIDATE_SYSTEM: str = (
    "You are a DAG prompt validator. Check if the technical_prompt is specific enough "
    "for autonomous multi-agent execution — no essential parameters should be ambiguous.\n\n"
    "Return ONLY this JSON — no prose:\n"
    '{"valid": true|false,\n'
    ' "missing": [],\n'
    ' "clarification_question": ""}'
)


@dataclasses.dataclass
class DagPrompt:
    """Structured execution instructions GOAT passes to the DAG planner.

    Attributes:
        task_id: UUID4 bound to this build call for traceability.
        technical_prompt: Self-contained objective for the DAG planner.
        required_agents: Roles the LLM selected from the registry's live roles.
        verification_criteria: Observable outcomes that prove task success.
        memory_updates: Whether agents should write progress to working memory.
        constraints: Task-specific limits (workspace, tier, TTL, etc.).
    """

    task_id: str
    technical_prompt: str
    required_agents: list[str]
    verification_criteria: list[str]
    memory_updates: bool
    constraints: dict


def _available_roles(registry: "Registry") -> list[str]:
    """Return the DAG agent roles registered in the registry.

    Discovered dynamically from ``registry.agent_registry`` — there is no
    hardcoded role list anywhere in this module. Returns an empty list if the
    registry cannot be queried, in which case the LLM selects from the roles it
    can infer from the tool hints.
    """
    try:
        return sorted(registry.agent_registry.roles())
    except Exception as exc:
        log.debug("_available_roles: roles() unavailable: %s", exc)
        return []


def _fallback_dag_prompt(intent: str) -> DagPrompt:
    """Return a minimal safe DagPrompt when the LLM call fails or returns bad JSON."""
    return DagPrompt(
        task_id=uuid.uuid4().hex,
        technical_prompt=intent,
        required_agents=[],
        verification_criteria=[],
        memory_updates=False,
        constraints={},
    )


async def build_dag_prompt(
    decision: "GoatDecision",
    mem_ctx: str,
    history_text: str,
    registry: "Registry",
) -> DagPrompt:
    """Format a GoatDecision into a DagPrompt via a single LLM call.

    The Prompter FORMATS — it does not decide. GOAT has already decided WHAT to
    execute (``decision.enriched_intent``), which tools to prefer
    (``decision.tool_hints``), the workspace facts (``decision.workspace_context``),
    and the constraints (``decision.constraints``). The LLM shapes those, plus the
    registry's live agent roles, into the DagPrompt schema — preserving every detail
    of the enriched instruction and selecting agents only from the available roles.
    No hardcoded rules, role lists, paths, or criteria templates. Falls back to a
    minimal DagPrompt wrapping the enriched intent on any failure.

    Args:
        decision: GOAT's enrichment decision for the user's intent.
        mem_ctx: Pre-computed memory context string from the working tier.
        history_text: Formatted recent conversation history.
        registry: ServiceRegistry for model configuration and role discovery.

    Returns:
        DagPrompt with LLM-selected agents, criteria, and technical objective.
    """
    spec = registry.settings.supervisor.model
    roles = _available_roles(registry)
    user_parts = [
        "Enriched instruction (already decided by GOAT — preserve EVERY detail):\n"
        f"{decision.enriched_intent}"
    ]
    if decision.tool_hints:
        user_parts.append("\nTool hints (guide agent selection):\n" + ", ".join(decision.tool_hints))
    if decision.workspace_context:
        user_parts.append(f"\nWorkspace context:\n{decision.workspace_context}")
    if decision.constraints:
        user_parts.append(f"\nConstraints from GOAT:\n{json.dumps(decision.constraints, ensure_ascii=False)}")
    if mem_ctx:
        user_parts.append(f"\nMemory context:\n{mem_ctx}")
    if history_text:
        user_parts.append(f"\nRecent conversation:\n{history_text}")
    if roles:
        user_parts.append("\nAvailable agent roles (pick required_agents only from these):\n" + ", ".join(roles))
    user_parts.append("\nFormat this decision into the DAG execution plan JSON.")

    try:
        raw = await _call_llm(
            spec,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": "\n".join(user_parts)},
            ],
            json_mode=(spec.provider == Provider.OPENAI),
        )
        data = _extract_json(raw)
        agents = [str(a) for a in data.get("required_agents", []) if str(a).strip()]
        # Fold GOAT's workspace context + constraints into the planner constraints.
        constraints = dict(decision.constraints)
        constraints.update(dict(data.get("constraints", {})))
        if decision.workspace_context:
            constraints.setdefault("workspace_context", decision.workspace_context)
        log.info("build_dag_prompt: technical_prompt=%.200s", data.get("technical_prompt", ""))
        log.debug("build_dag_prompt: agents=%s hints=%s criteria=%d roles=%s", agents,
                  decision.tool_hints, len(data.get("verification_criteria", [])), roles)
        return DagPrompt(
            task_id=uuid.uuid4().hex,
            technical_prompt=str(data.get("technical_prompt", decision.enriched_intent)) or decision.enriched_intent,
            required_agents=agents,
            verification_criteria=[str(c) for c in data.get("verification_criteria", [])],
            memory_updates=bool(data.get("memory_updates", False)),
            constraints=constraints,
        )
    except Exception as exc:
        log.warning("build_dag_prompt: LLM call or parse failed — using fallback: %s", exc)
        return _fallback_dag_prompt(decision.enriched_intent)


async def validate_dag_prompt(
    dag_prompt: DagPrompt,
    intent: str,
    registry: "Registry",
) -> "ClarityResult":
    """Validate a DagPrompt for completeness and specificity.

    Two checks, neither using a hardcoded role list or rule table:
    1. verification_criteria not empty — the DAG cannot confirm success without them.
    2. LLM check: technical_prompt is specific enough for autonomous execution.

    Returns ClarityResult(clear=True) on pass, or ClarityResult with a specific
    clarification_question on failure. Defaults to clear=True on any LLM error so
    validation never hard-blocks the pipeline.

    Args:
        dag_prompt: The DagPrompt to validate.
        intent: Original user intent (for LLM context).
        registry: ServiceRegistry for model configuration.

    Returns:
        ClarityResult indicating whether the DagPrompt is ready for execution.
    """
    from supervisor.pipeline.intent_clarity import ClarityResult

    # Check 1: verification_criteria must not be empty (structural, no hardcoded values).
    if not dag_prompt.verification_criteria:
        missing = ["no observable verification criteria — cannot confirm task completion"]
        question = "To proceed, what would a successful result for this task look like?"
        log.debug("validate_dag_prompt: empty verification_criteria task_id=%s", dag_prompt.task_id)
        return ClarityResult(clear=False, missing=missing, clarification_question=question)

    # Check 2: LLM specificity check on technical_prompt.
    spec = registry.settings.supervisor.model
    user = (
        f"Technical prompt: {dag_prompt.technical_prompt}\n"
        f"Required agents: {dag_prompt.required_agents}\n"
        f"Verification criteria: {dag_prompt.verification_criteria}\n"
        f"Original intent: {intent}\n\n"
        "Is this specific enough for autonomous execution? Return JSON."
    )
    try:
        raw = await _call_llm(spec, [
            {"role": "system", "content": _VALIDATE_SYSTEM},
            {"role": "user",   "content": user},
        ])
        raw_json = _extract_balanced_json(raw) if raw.strip() else ""
        if raw_json:
            data = raw_json if isinstance(raw_json, dict) else json.loads(raw_json)
            if not bool(data.get("valid", True)):
                missing_items = [str(m) for m in data.get("missing", [])]
                question = str(data.get("clarification_question", "")) or "Could you provide more details?"
                log.debug("validate_dag_prompt: LLM says invalid missing=%s", missing_items)
                return ClarityResult(clear=False, missing=missing_items, clarification_question=question)
    except Exception as e:
        log.debug("validate_dag_prompt: LLM check failed — defaulting to valid: %s", e)

    log.debug("validate_dag_prompt: passed task_id=%s agents=%s", dag_prompt.task_id, dag_prompt.required_agents)
    return ClarityResult(clear=True, missing=[], clarification_question="")
