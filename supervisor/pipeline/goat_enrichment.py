"""GOAT intent enrichment — GOAT decides WHAT to execute before the DAG runs.

Architecture contract: **GOAT decides → Prompter formats → DAG executes.**

``enrich_intent`` performs a single LLM call that turns a raw, possibly
under-specified user intent into a complete, self-contained ``GoatDecision``:
an enriched instruction, the relevant workspace context, tool hints, and
constraints. This is the *decision* stage — the downstream Prompter
(``dag_prompt_builder``) only *formats* this decision into a DagPrompt; it
must never guess what to execute.

No hardcoded paths, rules, regex, or keyword routing live here — every
decision is made by the LLM. The model is instructed to respond in the same
language as the user. On any failure the function degrades gracefully to a
decision that wraps the raw intent, so the pipeline is never hard-blocked.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from config.settings import Provider
from utils.llm_utils import _call_llm, _extract_json

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.supervisor.pipeline.goat_enrichment")

__all__ = ["GoatDecision", "enrich_intent"]

_SYSTEM: str = (
    "You are GOAT, a multi-agent supervisor. Your job is to DECIDE exactly what "
    "must be executed for the user's request — you are NOT formatting a prompt and "
    "you are NOT executing tools yourself. Turn the user's intent into a complete, "
    "specific, self-contained instruction that a downstream DAG of agents can run "
    "without guessing.\n\n"
    "Use the memory context, conversation history, and the list of available tools "
    "to resolve ambiguity: infer concrete targets (paths, files, URLs), the precise "
    "operation, and how success is verified. Reason purely from the inputs — never "
    "invent rules, never assume paths that are not supported by the context.\n\n"
    "Respond in the SAME LANGUAGE as the user.\n\n"
    "Return ONLY this JSON schema — no prose, no markdown:\n"
    "{\n"
    '  "enriched_intent": "<complete, specific instruction for the DAG: what to do, '
    'on which concrete target, and how to verify success>",\n'
    '  "workspace_context": "<relevant environment facts the DAG needs: resolved '
    'paths, file/URL targets, prior results — empty string if none>",\n'
    '  "tool_hints": ["<role:capability the DAG should prefer, e.g. tool_caller:file_read>", ...],\n'
    '  "constraints": {"<key>": "<value>"}\n'
    "}\n\n"
    "Rules:\n"
    "  - enriched_intent must be self-contained: do not reference this JSON or the "
    "conversation; restate every needed detail.\n"
    "  - tool_hints: choose ONLY from the available tools provided below; they are "
    "hints to guide agent selection, not commands.\n"
    "  - constraints: task-specific limits the DAG should respect (e.g. language, "
    "max_lines, workspace_path, read_only) — include 'language' set to the user's "
    "language.\n"
    "  - Do NOT include session IDs or DAG IDs anywhere."
)


@dataclasses.dataclass
class GoatDecision:
    """GOAT's execution decision for a single user intent.

    This is the contract between the decision stage (``enrich_intent``) and the
    formatting stage (``dag_prompt_builder.build_dag_prompt``).

    Attributes:
        enriched_intent: Complete, self-contained instruction for the DAG —
            what to do, on which concrete target, and how to verify success.
        workspace_context: Relevant environment facts (resolved paths, targets,
            prior results) the DAG needs; empty string when none apply.
        tool_hints: ``role:capability`` hints (e.g. ``tool_caller:file_read``)
            that guide required-agent selection — hints, never commands.
        constraints: Task-specific limits the DAG must respect (language,
            max_lines, workspace_path, read_only, ...).
    """

    enriched_intent: str
    workspace_context: str
    tool_hints: list[str]
    constraints: dict


def _available_tools(registry: "ServiceRegistry") -> str:
    """Build a human-readable list of available agent roles and tool names.

    Derived dynamically from the registry — no hardcoded agent or tool list.
    Pulls registered DAG roles and the file/memory ToolDefinition names so the
    LLM can ground ``tool_hints`` in what actually exists.

    Args:
        registry: ServiceRegistry exposing agent_registry and tool lists.

    Returns:
        A compact descriptive string for the enrichment prompt.
    """
    parts: list[str] = []
    try:
        roles = registry.agent_registry.roles()
        if roles:
            parts.append("Agent roles: " + ", ".join(sorted(roles)))
    except Exception as exc:  # noqa: BLE001 — context-building must never raise
        log.debug("_available_tools: roles() failed: %s", exc)
    for label, attr in (("file tools", "file_tools"), ("memory tools", "memory_tools")):
        try:
            tools = getattr(registry, attr, None) or []
            names = [getattr(t, "name", "") for t in tools if getattr(t, "name", "")]
            if names:
                parts.append(f"{label}: " + ", ".join(names))
        except Exception as exc:  # noqa: BLE001
            log.debug("_available_tools: %s failed: %s", attr, exc)
    return "\n".join(parts)


def _fallback_decision(intent: str) -> GoatDecision:
    """Return a safe GoatDecision wrapping the raw intent when the LLM fails."""
    log.debug("enrich_intent: using fallback decision for intent=%.80s", intent)
    return GoatDecision(
        enriched_intent=intent,
        workspace_context="",
        tool_hints=[],
        constraints={},
    )


async def enrich_intent(
    intent: str,
    mem_ctx: str,
    history: str,
    registry: "ServiceRegistry",
) -> GoatDecision:
    """Decide WHAT to execute for ``intent`` via a single LLM call.

    GOAT reasons over the raw intent, memory context, conversation history, and
    the registry's available tools to produce a complete, specific
    ``GoatDecision``. Pure LLM reasoning — no hardcoded rules, paths, or regex.
    The model answers in the user's language. Any error degrades to a fallback
    decision wrapping the raw intent so the pipeline is never hard-blocked.

    Args:
        intent: The user's original intent text.
        mem_ctx: Pre-computed memory context string from the working tier.
        history: Formatted recent conversation history.
        registry: ServiceRegistry for model configuration and tool discovery.

    Returns:
        GoatDecision with enriched_intent, workspace_context, tool_hints, and
        constraints.
    """
    spec = registry.settings.supervisor.model
    available = _available_tools(registry)
    user_parts = [f"User intent: {intent}"]
    if mem_ctx:
        user_parts.append(f"\nMemory context:\n{mem_ctx}")
    if history:
        user_parts.append(f"\nConversation history:\n{history}")
    if available:
        user_parts.append(f"\nAvailable tools:\n{available}")
    user_parts.append("\nDecide what must be executed. Return the JSON.")

    log.debug("enrich_intent: deciding intent=%.120s", intent)
    try:
        raw = await _call_llm(
            spec,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": "\n".join(user_parts)},
            ],
            json_mode=(spec.provider == Provider.OPENAI),
        )
        data = _extract_json(raw)
        decision = GoatDecision(
            enriched_intent=str(data.get("enriched_intent", intent)) or intent,
            workspace_context=str(data.get("workspace_context", "")),
            tool_hints=[str(h) for h in data.get("tool_hints", []) if str(h).strip()],
            constraints=dict(data.get("constraints", {})),
        )
        log.info(
            "enrich_intent: enriched=%.200s hints=%s",
            decision.enriched_intent, decision.tool_hints,
        )
        return decision
    except Exception as exc:  # noqa: BLE001 — enrichment must never hard-block
        log.warning("enrich_intent: LLM call or parse failed — using fallback: %s", exc)
        return _fallback_decision(intent)
