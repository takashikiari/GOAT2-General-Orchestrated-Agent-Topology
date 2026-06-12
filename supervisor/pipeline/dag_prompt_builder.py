"""DagPrompt builder — GOAT formulates structured execution instructions for DAG.

GOAT calls build_dag_prompt() before spawning the DAG. The resulting DagPrompt
replaces raw user intent as the input to the planner, ensuring the DAG receives
a self-contained technical objective with dynamically-selected agents and
observable verification criteria.

All decisions (required_agents, verification_criteria) are made by a single LLM
call — no hardcoded rules, patterns, or agent lists anywhere in this module.
"""
from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import TYPE_CHECKING

from config.settings import Provider
from utils.llm_utils import _call_llm, _extract_json

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.pipeline.dag_prompt_builder")

__all__ = ["DagPrompt", "build_dag_prompt"]

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
    "  - required_agents: infer from the task type; valid roles are researcher, "
    "coder, critic, summarizer, tool_caller; reason from the intent — do not use "
    "a fixed list\n"
    "  - verification_criteria: list 2–5 observable outcomes that prove the task "
    "succeeded (e.g. 'file was read', 'web search returned results', "
    "'code was written to disk')\n"
    "  - memory_updates: true when agents should write findings to working memory\n"
    "  - constraints: task-specific limits the planner should respect "
    "(max_tasks, language, workspace_path, tier_access, ttl)\n"
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
        return DagPrompt(
            task_id=uuid.uuid4().hex,
            technical_prompt=str(data.get("technical_prompt", intent)),
            required_agents=list(data.get("required_agents", [])),
            verification_criteria=list(data.get("verification_criteria", [])),
            memory_updates=bool(data.get("memory_updates", False)),
            constraints=dict(data.get("constraints", {})),
        )
    except Exception as exc:
        log.warning("build_dag_prompt: LLM call or parse failed — using fallback: %s", exc)
        return _fallback_dag_prompt(intent)
