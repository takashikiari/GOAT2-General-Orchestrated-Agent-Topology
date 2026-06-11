"""Intent depth classifier — pure LLM reasoning, no hardcoded keywords.

The classifier is the gatekeeper that decides whether a user message
should be answered directly (CONVERSATIONAL), run through a small DAG
(ANALYTICAL), or trigger a full multi-agent pipeline (COMPLEX).

CORE INVARIANT — NO HARDCODED KEYWORDS:
======================================
There are zero `re.compile(...)` patterns, zero `if "?" in text`
checks, zero greeting lists, zero help detection, and zero first-
message length heuristics. Every intent — including "?", "salut",
"help", and one-word messages — flows through the same semantic
LLM path. The model receives the intent plus full context (what
GOAT can do, what requires the DAG, conversation history, user
profile, active DAG sessions, user override, behavioral hints)
and decides on its own.

CORE INVARIANT — INTENTDEPTH ENUM:
==================================
`IntentDepth` is a 3-value enum:
  CONVERSATIONAL — GOAT answers directly with memory + web_search
  ANALYTICAL     — lightweight DAG (≤2 tasks)
  COMPLEX        — full DAG with planner, researcher, critic
Callers that import `IntentDepth` are unaffected. The classifier
returns the enum directly.

FALLBACK SAFEGUARD:
===================
If the LLM returns empty or unparseable output, the classifier
falls back to CONVERSATIONAL. This is a safe default — never
escalate to a full DAG on uncertainty.

REGISTRY INJECTION:
===================
classify_intent() requires `registry` parameter. Uses
registry.settings.agents.get("memory") for classification.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.classification")

if TYPE_CHECKING:
    from config.registry import Registry

__all__ = ["IntentDepth", "classify_intent"]


class IntentDepth(str, Enum):
    """Three-tier routing depth used by GoatSupervisor.run()."""

    CONVERSATIONAL = "conversational"  # direct LLM reply with tools available
    ANALYTICAL     = "analytical"      # lightweight DAG, ≤2 tasks
    COMPLEX        = "complex"         # full DAG with planner, researcher, critic


async def classify_intent(
    intent: str,
    registry: "Registry",
    is_first_message: bool = False,  # kept for API compatibility, not used
) -> IntentDepth:
    """Classify intent via LLM reasoning — pure semantic, no keywords.

    The LLM receives a single prompt containing:
      - GOAT's direct capabilities (memory + web_search)
      - What requires the DAG (multi-step research, code, deep analysis)
      - Current conversation history (recent user turns)
      - Active DAG sessions (wave/total/status)
      - User profile (semantic summary from long-term)
      - User override (if any — "force conversational" or "force complex")
      - Prior corrections (soft semantic hints from episodic memory)

    The model replies with exactly one word: conversational, analytical,
    or complex. On parse failure → CONVERSATIONAL (safe fallback).

    Args:
        intent: The raw user message text.
        registry: ServiceRegistry for settings and memory access.
        is_first_message: Kept for API compatibility. Not consulted
                          by the LLM — the model reasons about the
                          content of the message on its own merits.

    Returns:
        IntentDepth.CONVERSATIONAL | ANALYTICAL | COMPLEX
    """
    # Lazy imports keep this module's runtime cost minimal and avoid
    # any chance of a circular import via the supervisor/ package.
    from supervisor.classification.classifier_prompt import (
        build_classifier_prompt,
        _CLASSIFIER_SYSTEM,
    )
    from supervisor.classification.classifier_context import (
        detect_override,
        gather_active_dags,
        gather_user_profile,
        gather_hints,
    )
    from utils.llm_utils import _call_llm

    # ── Step 1: detect explicit user override (semantic) ──
    override = await detect_override(intent, registry)

    # ── Step 2: gather context for the LLM prompt ──
    history_text, active_dags, user_profile, hints = await _gather_all(registry)

    # ── Step 3: build the user prompt with all context ──
    user_prompt = build_classifier_prompt(
        intent=intent,
        history_text=history_text,
        active_dags=active_dags,
        user_profile=user_profile,
        override=override,
        hints=hints,
    )

    # ── Step 4: call the LLM ──
    settings = registry.settings
    try:
        raw = await _call_llm(
            settings.agents.get("memory"),
            [
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as e:
        log.warning("classify_intent LLM call failed, falling back to CONVERSATIONAL: %s", e)
        return IntentDepth.CONVERSATIONAL

    # ── Step 5: parse + apply override (override always wins) ──
    token = raw.strip().lower().split()[0] if raw.strip() else ""
    log.debug(
        "classify_intent: intent=%.80s override=%s llm_token=%r",
        intent, override, token,
    )

    if override == "conversational":
        return IntentDepth.CONVERSATIONAL
    if override == "complex":
        return IntentDepth.COMPLEX

    try:
        return IntentDepth(token)
    except ValueError:
        # LLM returned garbage — fall back to CONVERSATIONAL
        return IntentDepth.CONVERSATIONAL


async def _gather_all(registry: "Registry") -> tuple[str, list[dict], str, list[str]]:
    """Gather all context fields needed for the classifier prompt."""
    from supervisor.classification.classifier_context import (
        gather_active_dags,
        gather_user_profile,
        gather_hints,
    )
    from supervisor.classification.classifier_prompt import format_history
    try:
        from supervisor.session.history import ConversationHistory
        hist = getattr(registry, "_history", None) or ConversationHistory()
        history_text = format_history(hist.messages)
    except Exception:
        history_text = "(no prior conversation)"

    active_dags = await gather_active_dags(registry)
    user_profile = await gather_user_profile(registry)
    hints = await gather_hints(registry)
    return history_text, active_dags, user_profile, hints
