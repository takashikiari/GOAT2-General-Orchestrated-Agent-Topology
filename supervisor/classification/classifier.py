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

STRUCTURED OUTPUT:
==================
The LLM returns a JSON object:
  {"intent": "conversational"|"simple"|"complex",
   "confidence": 0.0-1.0,
   "reasoning": "brief explanation",
   "scores": {"complexity": 0.0-1.0, "tool_requirement": 0.0-1.0,
               "context_dependency": 0.0-1.0}}
"simple" maps to IntentDepth.ANALYTICAL. The full JSON is written to
working memory at goat:<session_id>:intent_classification (TTL 300s).

FALLBACK SAFEGUARD:
===================
If the LLM returns empty or unparseable output, the classifier
falls back to CONVERSATIONAL. This is a safe default — never
escalate to a full DAG on uncertainty.

REGISTRY INJECTION:
===================
classify_intent() requires `registry` parameter. Uses
registry.settings.agents.get("memory") for classification.

PER-REQUEST STATE:
==================
The conversation history is passed as an explicit `history`
parameter (a `ConversationHistory` instance or None). The
registry is never mutated with per-request state — `ServiceRegistry`
uses `__slots__` and would reject dynamic attribute assignment.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

log = logging.getLogger("goat2.supervisor.classification.classifier")

if TYPE_CHECKING:
    from config.registry import Registry
    from supervisor.session.history import ConversationHistory

__all__ = ["IntentDepth", "classify_intent"]


class IntentDepth(str, Enum):
    """Three-tier routing depth used by GoatSupervisor.run()."""

    CONVERSATIONAL = "conversational"  # direct LLM reply with tools available
    ANALYTICAL     = "analytical"      # lightweight DAG, ≤2 tasks
    COMPLEX        = "complex"         # full DAG with planner, researcher, critic


async def classify_intent(
    intent: str,
    registry: "Registry",
    history: "ConversationHistory | None" = None,
    is_first_message: bool = False,  # kept for API compatibility, not used
    session_id: str | None = None,
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

    The model replies with a JSON object containing intent, confidence,
    reasoning, and per-dimension scores. On parse failure → CONVERSATIONAL.
    The full JSON is written to goat:<session_id>:intent_classification
    with a 300s TTL when session_id is provided.

    Args:
        intent: The raw user message text.
        registry: ServiceRegistry for settings and memory access.
        history: The current ConversationHistory instance. Passed
                 explicitly so the registry stays stateless (it
                 uses __slots__ and cannot accept dynamic attrs).
                 May be None — the classifier then uses a fresh
                 empty history.
        is_first_message: Kept for API compatibility. Not consulted
                          by the LLM — the model reasons about the
                          content of the message on its own merits.
        session_id: GOAT session ID for writing classification result
                    to working memory. No write if None.

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
    )
    from utils.llm_utils import _call_llm

    # ── Step 1: detect explicit user override (semantic) ──
    override = await detect_override(intent, registry)

    # ── Step 2: gather context for the LLM prompt ──
    history_text, active_dags, user_profile, hints = await _gather_all(
        registry, history,
    )
    log.debug("classify_intent: active_dags=%d intent=%.80s", len(active_dags), intent)

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

    # ── Step 5: parse JSON output ──
    classification_json: dict | None = None
    token = ""
    try:
        from utils.llm_utils import _extract_balanced_json
        raw_json = _extract_balanced_json(raw) if raw.strip() else ""
        if raw_json:
            classification_json = json.loads(raw_json)
            token = str(classification_json.get("intent", "")).strip().lower()
            if token == "simple":
                token = "analytical"  # map to IntentDepth enum value
    except Exception:
        token = raw.strip().lower().split()[0] if raw.strip() else ""

    log.debug(
        "classify_intent: intent=%.80s override=%s token=%r confidence=%s",
        intent, override, token,
        classification_json.get("confidence") if classification_json else "n/a",
    )

    # ── Step 6: persist classification to working memory ──
    mm = getattr(registry, "memory_manager", None)
    if session_id and classification_json and mm:
        try:
            from config.roles import SESSION_ROLE
            key = f"goat:{session_id}:intent_classification"
            await mm.working.store(SESSION_ROLE, key, json.dumps(classification_json), ttl=300)
            log.debug("classify_intent: wrote classification key=%s", key)
        except Exception as e:
            log.debug("classify_intent: working memory write failed: %s", e)

    # ── Step 7: apply override (override always wins) ──
    if override == "conversational":
        return IntentDepth.CONVERSATIONAL
    if override == "complex":
        return IntentDepth.COMPLEX

    try:
        return IntentDepth(token)
    except ValueError:
        log.debug("classify_intent: unrecognised token %r — falling back to CONVERSATIONAL", token)
        return IntentDepth.CONVERSATIONAL


async def _gather_all(
    registry: "Registry",
    history: "ConversationHistory | None",
) -> tuple[str, list[dict[str, Any]], str, list[str]]:
    """Gather all context fields needed for the classifier prompt.

    Args:
        registry: ServiceRegistry for memory access.
        history: ConversationHistory passed explicitly by the caller.
                 A fresh empty history is used if None is supplied.

    Returns:
        (history_text, active_dags, user_profile, hints)
    """
    from supervisor.classification.classifier_context import (
        gather_active_dags,
        gather_user_profile,
        gather_hints,
    )
    from supervisor.classification.classifier_prompt import format_history
    try:
        if history is None:
            from supervisor.session.history import ConversationHistory
            history = ConversationHistory()
        history_text = format_history(history.messages)
    except Exception:
        history_text = "(no prior conversation)"

    active_dags = await gather_active_dags(registry)
    user_profile = await gather_user_profile(registry)
    hints = await gather_hints(registry)
    return history_text, active_dags, user_profile, hints
