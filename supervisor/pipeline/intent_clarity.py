"""Intent clarity check — GOAT asks the LLM to SCORE how clear an intent is.

A single LLM call returns a ClarityResult carrying a continuous ``clarity_score``
(0.0–1.0), a list of missing details, and a specific clarification_question. The
score replaces the old binary clear/unclear decision, which over-triggered on
short messages that were obvious in context (e.g. "raport", "memory check", "da").

Score bands (the LLM assigns the score; GOAT interprets the bands):
  - 0.8–1.0: clear — proceed to the DAG.
  - 0.5–0.79: mostly clear — GOAT completes from context and proceeds with a warning.
  - 0.0–0.49: genuinely ambiguous — ask the user for clarification.

``ClarityResult.clear`` is derived as ``clarity_score >= CLARITY_THRESHOLD`` (0.5),
kept for backward compatibility. ``CLARITY_THRESHOLD`` (0.5) is the only hardcoded
numeric gate; everything else is pure LLM scoring — no keyword matching, no pattern
lists, no length heuristics, no regex.
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

__all__ = [
    "ClarityResult",
    "check_intent_clarity",
    "CLARITY_THRESHOLD",
    "CLARITY_CONFIDENT",
]

# The ONLY hardcoded numeric gate: a clarity_score below this blocks the DAG and
# asks the user to clarify. At or above it, GOAT proceeds.
CLARITY_THRESHOLD: float = 0.5
# At or above this, the intent is fully clear; in [CLARITY_THRESHOLD, CLARITY_CONFIDENT)
# GOAT completes from context and proceeds with a warning. This boundary only chooses
# whether to log a warning — it changes no execution path.
CLARITY_CONFIDENT: float = 0.8

_SYSTEM: str = (
    "You are GOAT's intent clarity scorer. Assign a continuous clarity_score from 0.0 to "
    "1.0 indicating how ready a user's CURRENT message is for a multi-agent DAG to execute.\n\n"
    "REASON IN CONTEXT — THIS IS THE MOST IMPORTANT RULE. You are given the recent "
    "conversation (both user and assistant turns) and memory context. Interpret the current "
    "message IN LIGHT OF that conversation, never in isolation. A short message is CLEAR when "
    "the conversation makes its meaning unambiguous: if the assistant just offered a report and "
    "the user says 'raport' or 'da', that is a clear instruction (score HIGH). Only score low "
    "when nothing in the conversation or memory resolves what the user wants.\n\n"
    "Score bands (you assign the score; reason semantically — no keyword or length rules):\n"
    "  - 0.8–1.0: clear — the message, read in context, is specific enough to execute now.\n"
    "  - 0.5–0.79: mostly clear — minor gaps the DAG can reasonably fill from memory/context.\n"
    "  - 0.0–0.49: genuinely ambiguous — even WITH the conversation and memory, what to do "
    "could mean anything, or critical details are missing and unresolvable from context.\n\n"
    "Do NOT penalize brevity. 'raport', 'memory check', 'da' score HIGH whenever history "
    "supplies the referent. Prefer letting the DAG try and fail usefully over blocking early.\n"
    "Always respond in the same language as the user message. If the user writes in Romanian, "
    "respond in Romanian.\n\n"
    "Return ONLY this JSON — no prose, no markdown:\n"
    '{"clarity_score": 0.0,\n'
    ' "missing": ["list of missing details that lower the score"],\n'
    ' "clarification_question": "specific question to ask if score < 0.5 (empty string otherwise)"}'
)


@dataclasses.dataclass
class ClarityResult:
    """Result of an intent clarity or DagPrompt validation check.

    Attributes:
        clear: True when intent/prompt is specific enough for DAG execution.
            Derived as ``clarity_score >= CLARITY_THRESHOLD``; kept for backward
            compatibility with callers that branch on a boolean.
        clarity_score: Continuous 0.0–1.0 readiness score assigned by the LLM.
            Defaults to 1.0 so existing constructors (DagPrompt validation,
            gates) that omit it are treated as fully clear.
        missing: Details the DAG would need to guess if not clear.
        clarification_question: Ready-to-send question for the user (empty when clear).
    """

    clear: bool
    clarity_score: float = 1.0
    missing: list[str] = dataclasses.field(default_factory=list)
    clarification_question: str = ""

    def __bool__(self) -> bool:
        """Allow use as a boolean — True when clear."""
        return self.clear


_CLEAR_DEFAULT = ClarityResult(
    clear=True, clarity_score=1.0, missing=[], clarification_question=""
)


def _parse_score(data: dict) -> float:
    """Extract a clamped 0.0–1.0 clarity_score from an LLM JSON response.

    Reads ``clarity_score`` and clamps it to [0.0, 1.0]. For backward
    compatibility with a model that still emits the legacy boolean ``clear``,
    derives the score from that flag (True→1.0, False→0.0) when ``clarity_score``
    is absent. Defaults to 1.0 (clear) on missing/garbage input so a malformed
    response never over-blocks.
    """
    raw = data.get("clarity_score")
    if raw is None:
        return 1.0 if bool(data.get("clear", True)) else 0.0
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        log.debug("intent_clarity: unparseable clarity_score=%r — defaulting to 1.0", raw)
        return 1.0


async def check_intent_clarity(
    intent: str,
    mem_ctx: str,
    history_text: str,
    registry: "Registry",
) -> ClarityResult:
    """Score how clear the intent is, reasoning IN CONVERSATIONAL CONTEXT.

    The LLM receives the recent dialogue (both speakers) and memory context and
    interprets the current message in light of them — a short message like
    "raport" or "da" scores high when the conversation supplies its referent.
    The model assigns ``clarity_score`` (0.0–1.0); ``clear`` is derived as
    ``clarity_score >= CLARITY_THRESHOLD``. Defaults to clear=True (score 1.0) on
    any failure or unexpected LLM output so ambiguity never hard-blocks.

    Args:
        intent: The user's current message text.
        mem_ctx: Pre-computed working-memory context (may resolve ambiguities).
        history_text: Recent dialogue, both user and assistant turns (~5 turns).
            Build it with ``classifier_prompt.format_dialogue`` so the LLM can
            reason about the message in context rather than in isolation.
        registry: ServiceRegistry for model configuration.

    Returns:
        ClarityResult with clear flag, clarity_score, missing info, and a
        specific clarification question.
    """
    # If DAG is forbidden by user override → always unclear (force conversational)
    try:
        from config.roles import SESSION_ROLE
        from memory.shared.types import MemoryKey
        key = MemoryKey("dag_constraint_execution_rule")
        record = await registry.memory_manager.working.backend.get(SESSION_ROLE, key)
        if record:
            log.debug("intent_clarity: DAG forbidden by user override — score=0.0")
            return ClarityResult(
                clear=False,
                clarity_score=0.0,
                missing=["dag execution disabled by user override"],
                clarification_question="DAG execution is currently disabled. How can I help you directly?",
            )
    except Exception:
        pass

    spec = registry.settings.supervisor.model
    # Present the conversation and memory as CONTEXT first, then the message to score,
    # so the LLM interprets the current message in light of the dialogue — not in isolation.
    user_parts: list[str] = []
    if history_text:
        user_parts.append(f"Conversation so far (oldest first, both speakers):\n{history_text}")
    if mem_ctx:
        user_parts.append(f"\nMemory context (may fill in gaps):\n{mem_ctx}")
    user_parts.append(f"\nCurrent user message to score: {intent}")
    user_parts.append(
        "\nInterpret the current message IN CONTEXT of the conversation and memory above. "
        "A short message is clear if the context resolves what the user wants. "
        "Score how ready it is for execution. Return JSON."
    )

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
        score = _parse_score(data)
        missing = [str(m) for m in data.get("missing", [])]
        question = str(data.get("clarification_question", ""))
        is_clear = score >= CLARITY_THRESHOLD
        log.debug("intent_clarity: score=%.2f clear=%s missing=%s intent=%.80s",
                  score, is_clear, missing, intent)
        return ClarityResult(
            clear=is_clear, clarity_score=score, missing=missing,
            clarification_question=question,
        )
    except Exception as exc:
        log.warning("check_intent_clarity: failed — defaulting to clear: %s", exc)
        return _CLEAR_DEFAULT
