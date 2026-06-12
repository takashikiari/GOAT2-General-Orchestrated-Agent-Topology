"""Intent clarity check — GOAT asks LLM whether an intent is clear enough for DAG execution.

A single LLM call returns a ClarityResult with a clear flag, list of missing details,
and a specific clarification_question. If unclear, GoatSupervisor returns the
clarification_question directly as its response instead of spawning the DAG.

All ambiguity judgment is delegated entirely to the LLM — no keyword matching,
no pattern lists, no length heuristics.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import TYPE_CHECKING

from config.timeouts import TURN_TIMEOUT
from utils.llm_utils import _call_llm, _extract_balanced_json

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.pipeline.intent_clarity")

__all__ = ["ClarityResult", "check_intent_clarity"]

_SYSTEM: str = (
    "You are GOAT's intent clarity checker. Decide whether a user's intent is specific "
    "enough for a multi-agent DAG to execute without further clarification.\n\n"
    "Consider an intent unclear ONLY when the DAG would need to guess essential "
    "parameters — target file path, desired scope, timeframe, or subject — that "
    "are not resolvable from memory context or conversation history.\n"
    "Reason semantically. Do not pattern-match on syntax or length.\n"
    "Always respond in the same language as the user message. If the user writes in Romanian, respond in Romanian.\n\n"
    "Return ONLY this JSON — no prose, no markdown:\n"
    '{"clear": true|false,\n'
    ' "missing": ["list of missing details that prevent execution"],\n'
    ' "clarification_question": "specific question to ask the user (empty string if clear)"}'
)


@dataclasses.dataclass
class ClarityResult:
    """Result of an intent clarity or DagPrompt validation check.

    Attributes:
        clear: True when intent or prompt is specific enough for DAG execution.
        missing: Details the DAG would need to guess if not clear.
        clarification_question: Ready-to-send question for the user (empty when clear).
    """

    clear: bool
    missing: list[str]
    clarification_question: str

    def __bool__(self) -> bool:
        """Allow use as a boolean — True when clear."""
        return self.clear


_CLEAR_DEFAULT = ClarityResult(clear=True, missing=[], clarification_question="")


async def check_intent_clarity(
    intent: str,
    mem_ctx: str,
    history_text: str,
    registry: "Registry",
) -> ClarityResult:
    """Ask the LLM whether the intent is clear enough for DAG execution.

    Returns ClarityResult(clear=True) when the DAG can proceed. Defaults to
    clear=True on any failure or unexpected LLM output so ambiguity never
    hard-blocks the pipeline.

    Args:
        intent: The user's intent text.
        mem_ctx: Pre-computed working-memory context (may resolve ambiguities).
        history_text: Formatted recent conversation history.
        registry: ServiceRegistry for model configuration.

    Returns:
        ClarityResult with clear flag, missing info list, and specific clarification question.
    """
    # If DAG is forbidden by user override → always unclear (force conversational)
    try:
        from config.roles import SESSION_ROLE
        from memory.shared.types import MemoryKey
        key = MemoryKey("dag_constraint_execution_rule")
        record = await registry.memory_manager.working.backend.get(SESSION_ROLE, key)
        if record:
            log.debug("intent_clarity: DAG forbidden by user override")
            return ClarityResult(
                clear=False,
                missing=["dag execution disabled by user override"],
                clarification_question="DAG execution is currently disabled. How can I help you directly?",
            )
    except Exception:
        pass

    spec = registry.settings.supervisor.model
    user_parts = [f"Intent: {intent}"]
    if mem_ctx:
        user_parts.append(f"\nMemory context (may fill in gaps):\n{mem_ctx}")
    if history_text:
        user_parts.append(f"\nRecent conversation:\n{history_text}")
    user_parts.append("\nIs this intent clear enough to execute? Return JSON.")

    try:
        raw = await asyncio.wait_for(
            _call_llm(
                spec,
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": "\n".join(user_parts)},
                ],
            ),
            timeout=TURN_TIMEOUT,
        )
        raw_json = _extract_balanced_json(raw) if raw.strip() else ""
        if not raw_json:
            log.debug("intent_clarity: no JSON in response — defaulting to clear")
            return _CLEAR_DEFAULT
        data = raw_json if isinstance(raw_json, dict) else json.loads(raw_json)
        is_clear = bool(data.get("clear", True))
        missing = [str(m) for m in data.get("missing", [])]
        question = str(data.get("clarification_question", ""))
        log.debug("intent_clarity: clear=%s missing=%s intent=%.80s", is_clear, missing, intent)
        return ClarityResult(clear=is_clear, missing=missing, clarification_question=question)
    except Exception as exc:
        log.warning("check_intent_clarity: failed — defaulting to clear: %s", exc)
        return _CLEAR_DEFAULT
