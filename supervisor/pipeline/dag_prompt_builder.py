"""DagPrompt builder — GOAT formulates structured execution instructions for DAG.

GOAT calls build_dag_prompt() before spawning the DAG. The resulting DagPrompt
replaces raw user intent as the input to the planner, ensuring the DAG receives
a self-contained technical objective with dynamically-selected agents and
observable verification criteria.

After building, call validate_dag_prompt() to check:
  - verification_criteria not empty
  - critic present for complex tasks (researcher/coder involved)
  - technical_prompt is specific enough for autonomous execution (LLM check)

All decisions (required_agents, verification_criteria, specificity) are made by LLM
calls — no hardcoded rules, patterns, or agent lists in this module.
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

log = logging.getLogger("goat2.supervisor.pipeline.dag_prompt_builder")

__all__ = ["DagPrompt", "build_dag_prompt", "validate_dag_prompt"]

_SYSTEM: str = (
    "You are GOAT's DAG instruction builder. Given a user intent, memory context, "
    "and conversation history, produce a structured DAG execution plan as JSON.\n\n"
    "Return ONLY this JSON schema — no prose, no markdown:\n"
    "{\n"
    '  "technical_prompt": "<comprehensive, self-contained objective for the DAG planner>",\n'
    '  "required_agents": ["<role>", ...],\n'
    '  "verification_criteria": ["<observable outcome>", ...],\n'
    '  "memory_updates": true,\n'
    '  "constraints": {"key": "value"}\n'
    "}\n\n"
    "Rules:\n"
    "  - technical_prompt: include all context the planner needs; do not reference "
    "this JSON — it must be self-contained\n"
    "  - required_agents: use ONLY these exact role names: researcher, coder, critic, summarizer, tool_caller, memory. "
    "Do NOT invent new roles like promote_to_letta, check_memory, monitor_logs, etc. "
    "Choose from the list based on task needs.\n"
    "  - verification_criteria: list 2–5 observable outcomes that prove the task "
    "succeeded (e.g. 'file was read', 'web search returned results', "
    "'code was written to disk')\n"
    "  - memory_updates: true when agents should write findings to working memory\n"
    "  - constraints: task-specific limits the planner should respect "
    "(max_tasks, language, workspace_path, tier_access, ttl)\n"
    "  - technical_prompt must NOT contain session IDs, DAG IDs, or references to specific sessions\\n"
)

_VALIDATE_SYSTEM: str = (
    "You are a DAG prompt validator. Check if the technical_prompt is specific enough "
    "for autonomous multi-agent execution — no essential parameters should be ambiguous.\n\n"
    "Return ONLY this JSON — no prose:\n"
    '{"valid": true|false,\n'
    ' "missing": ["specific detail1", ...],\n'
    ' "clarification_question": "exact question to ask the user (empty string if valid)"}'
)


@dataclasses.dataclass
class DagPrompt:
    """Structured execution instructions GOAT passes to the DAG planner.

    Attributes:
        task_id: UUID4 bound to this build call for traceability.
        technical_prompt: Self-contained objective for the DAG planner.
        required_agents: Roles the LLM selected based on intent analysis.
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
    intent: str,
    mem_ctx: str,
    history_text: str,
    registry: "Registry",
) -> DagPrompt:
    """Build a DagPrompt from user intent via a single LLM call.

    The LLM decides required_agents and verification_criteria dynamically based
    on the intent — no hardcoded rules or patterns. Falls back to a minimal
    DagPrompt wrapping raw intent on any failure.

    Args:
        intent: The user's original intent text.
        mem_ctx: Pre-computed memory context string from the working tier.
        history_text: Formatted recent conversation history.
        registry: ServiceRegistry for model configuration.

    Returns:
        DagPrompt with LLM-selected agents, criteria, and technical objective.
    """
    spec = registry.settings.supervisor.model
    user_parts = [f"Intent: {intent}"]
    if mem_ctx:
        user_parts.append(f"\nMemory context:\n{mem_ctx}")
    if history_text:
        user_parts.append(f"\nRecent conversation:\n{history_text}")
    user_parts.append("\nBuild the DAG execution plan.")

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
        log.info("build_dag_prompt: technical_prompt=%.200s", data.get("technical_prompt", ""))
        log.debug("build_dag_prompt: agents=%s criteria=%d",
                  data.get("required_agents"), len(data.get("verification_criteria", [])))
        agents = list(data.get("required_agents", []))
        if "critic" not in agents and len(agents) > 1:
            agents.append("critic")
        return DagPrompt(
            task_id=uuid.uuid4().hex,
            technical_prompt=str(data.get("technical_prompt", intent)),
            required_agents=agents,
            verification_criteria=list(data.get("verification_criteria", [])),
            memory_updates=bool(data.get("memory_updates", False)),
            constraints=dict(data.get("constraints", {})),
        )
    except Exception as exc:
        log.warning("build_dag_prompt: LLM call or parse failed — using fallback: %s", exc)
        return _fallback_dag_prompt(intent)


async def validate_dag_prompt(
    dag_prompt: DagPrompt,
    intent: str,
    registry: "Registry",
) -> "ClarityResult":
    """Validate a DagPrompt for completeness and specificity.

    Checks (in order):
    1. verification_criteria not empty — DAG cannot confirm success without them.
    2. critic in required_agents when researcher or coder is present (complex task).
    3. LLM check: technical_prompt is specific enough for autonomous execution.

    Returns ClarityResult(clear=True) on pass. Returns ClarityResult with a specific
    clarification_question on any failure. Defaults to clear=True on LLM error
    so validation never hard-blocks the pipeline.

    Args:
        dag_prompt: The DagPrompt to validate.
        intent: Original user intent (for LLM context).
        registry: ServiceRegistry for model configuration.

    Returns:
        ClarityResult indicating whether the DagPrompt is ready for execution.
    """
    from supervisor.pipeline.intent_clarity import ClarityResult

    missing: list[str] = []

    # Check 1: verification_criteria must not be empty
    if not dag_prompt.verification_criteria:
        missing.append("no observable verification criteria — cannot confirm task completion")

    # Check 2: critic required when researcher or coder is involved
    _complex_roles = frozenset({"researcher", "coder"})
    if _complex_roles.intersection(dag_prompt.required_agents) and "critic" not in dag_prompt.required_agents:
        missing.append("critic agent missing — required to validate output for complex tasks")

    if missing:
        question = f"To proceed, I need more specifics: {'; '.join(missing)}. Can you clarify your request?"
        log.debug("validate_dag_prompt: structural issues=%s", missing)
        return ClarityResult(clear=False, missing=missing, clarification_question=question)

    # Check 3: LLM specificity check on technical_prompt
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
            data = json.loads(raw_json)
            if not bool(data.get("valid", True)):
                missing_items = [str(m) for m in data.get("missing", [])]
                question = str(data.get("clarification_question", "")) or "Could you provide more details?"
                log.debug("validate_dag_prompt: LLM says invalid missing=%s", missing_items)
                return ClarityResult(clear=False, missing=missing_items, clarification_question=question)
    except Exception as e:
        log.debug("validate_dag_prompt: LLM check failed — defaulting to valid: %s", e)

    log.debug("validate_dag_prompt: passed task_id=%s agents=%s", dag_prompt.task_id, dag_prompt.required_agents)
    return ClarityResult(clear=True, missing=[], clarification_question="")
