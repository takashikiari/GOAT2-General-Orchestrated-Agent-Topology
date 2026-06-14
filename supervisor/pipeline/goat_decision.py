"""The single GOAT decision call — one LLM, one prompt, decides everything.

This replaces the former 6-call routing pipeline (override, disagreement,
classify, clarity, enrichment, dag-prompt build/validate) — each of which had its
own conflicting guardrails — with **one** LLM call. Middleware only assembles
context (no LLM); this call reasons over all of it and returns a structured
decision. DAG agents keep their own specialized LLMs downstream.

GOAT returns one of three actions:
  - ``direct``  — answer conversationally (the supervisor then runs the existing
                  tool-enabled reply so memory/web tools are available).
  - ``clarify`` — the request is ambiguous; ``clarification`` holds the question.
  - ``dag``     — the task needs the multi-agent pipeline; ``dag_instructions``
                  holds the self-contained objective for the planner.

Pure LLM reasoning — no keywords, regex, hardcoded rules, or thresholds.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from typing import TYPE_CHECKING

from config.settings import Provider
from utils.llm_utils import _call_llm, _extract_json

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from supervisor.pipeline.goat_enrichment import GoatContext
    from supervisor.pipeline.intent_clarity import ClarityContext

log = logging.getLogger("goat2.supervisor.pipeline.goat_decision")

__all__ = ["GoatDecision", "decide"]

_VALID_ACTIONS = ("direct", "clarify", "dag")

# Last-resort regex: extract action from malformed JSON without a full parse.
_ACTION_RE = re.compile(r'"action"\s*:\s*"(direct|clarify|dag)"')


def _fallback_action(raw: str) -> str | None:
    """Extract action from raw text when JSON parsing fails completely."""
    m = _ACTION_RE.search(raw)
    return m.group(1) if m else None

_GOAT_SYSTEM: str = (
    "You are GOAT, a multi-agent supervisor. You receive the full context (your "
    "capabilities and tools, the workspace, memory, the conversation so far, and any "
    "past user corrections) and the user's current message. Decide, in ONE step, how "
    "to handle it. Reason purely from the context — no fixed rules.\n\n"
    "Choose exactly one action:\n"
    "  - direct: you can answer now (conversation, a memory/web lookup, a simple "
    "request). The system will let you use tools when you actually answer.\n"
    "  - clarify: the request is genuinely ambiguous and you cannot proceed without "
    "more information — ask one specific question.\n"
    "  - dag: the task needs the multi-agent pipeline (multi-step research, code, "
    "deep analysis). Write self-contained instructions for the planner.\n\n"
    "Prefer direct for ordinary messages; only choose dag when the work genuinely "
    "needs multiple specialized agents, and only choose clarify when you truly cannot "
    "act. Respond in the user's language.\n\n"
    "Return ONLY this JSON — no prose, no markdown:\n"
    "{\n"
    '  "action": "direct | clarify | dag",\n'
    '  "response": "<your direct answer, if action=direct; else empty>",\n'
    '  "clarification": "<your question, if action=clarify; else empty>",\n'
    '  "dag_instructions": "<self-contained planner objective, if action=dag; else empty>"\n'
    "}"
)


@dataclasses.dataclass
class GoatDecision:
    """GOAT's single-call routing decision.

    Attributes:
        action: One of ``direct``, ``clarify``, ``dag``.
        response: Direct conversational answer (used as a fallback; the supervisor
            regenerates a tool-enabled reply for ``direct``).
        clarification: The question to ask the user when ``action == "clarify"``.
        dag_instructions: Self-contained objective for the planner when
            ``action == "dag"``.
    """

    action: str
    response: str = ""
    clarification: str = ""
    dag_instructions: str = ""


def _build_user_prompt(
    intent: str, goat_context: "GoatContext", clarity_context: "ClarityContext", hints: list[str]
) -> str:
    """Assemble the decision prompt from the pure-context builders' output."""
    parts = [f"User message: {intent}", "", goat_context.to_prompt(), clarity_context.to_prompt()]
    if hints:
        parts.append("Past user corrections (soft hints):\n" + "\n".join(f"- {h}" for h in hints))
    parts.append("\nDecide the action and return the JSON.")
    return "\n".join(parts)


async def decide(
    registry: "ServiceRegistry",
    intent: str,
    goat_context: "GoatContext",
    clarity_context: "ClarityContext",
    hints: list[str],
) -> GoatDecision:
    """Make the single GOAT routing decision via one LLM call.

    Args:
        registry: ServiceRegistry for the model spec.
        intent: The user's current message.
        goat_context: Pure-built facts (workspace, tools, memory).
        clarity_context: Pure-built conversation/clarity context.
        hints: Past-correction hint strings (pure-built).

    Returns:
        GoatDecision. On any LLM/parse failure → ``action="direct"`` (safe default;
        never auto-escalate to a DAG on uncertainty).
    """
    spec = registry.settings.supervisor.model
    user_prompt = _build_user_prompt(intent, goat_context, clarity_context, hints)
    raw = ""
    try:
        raw = await _call_llm(
            spec,
            [
                {"role": "system", "content": _GOAT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=(spec.provider == Provider.OPENAI),
        )
        data = _extract_json(raw)
        action = str(data.get("action", "direct")).strip().lower()
        if action not in _VALID_ACTIONS:
            log.debug("decide: unrecognised action %r — defaulting to direct", action)
            action = "direct"
        decision = GoatDecision(
            action=action,
            response=str(data.get("response", "")),
            clarification=str(data.get("clarification", "")),
            dag_instructions=str(data.get("dag_instructions", "")),
        )
        log.info("decide: action=%s intent=%.80s", decision.action, intent)
        return decision
    except Exception as exc:
        # Try to recover the action even from malformed JSON via regex.
        rescued = _fallback_action(raw) if raw else None
        log.warning(
            "decide: parse failed (rescued=%s) raw=%.300s error=%s",
            rescued or "none", raw[:300] if raw else "N/A", exc,
        )
        return GoatDecision(action=rescued or "direct")
