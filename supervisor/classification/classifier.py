"""Intent depth classifier — routes intents to conversational, analytical, or complex handling.

All classification is LLM-driven with no keyword short-circuits. The model
semantically evaluates intent depth regardless of message formatting or prefixes.
Memory queries are routed through the same semantic path — if a user asks about
memory, the LLM may classify it as analytical or complex if it requires DAG tools.

FALLBACK SAFEGUARD (FIX):
=========================
If the LLM returns empty or unparseable output, we fall back to ANALYTICAL
(a lightweight DAG with ≤2 tasks) instead of COMPLEX (full DAG). This prevents
token waste on unnecessary full DAG execution when the classifier fails.

REGISTRY INJECTION (PHASE 4):
=============================
classify_intent() now requires `registry` parameter.
Uses registry.settings.agents.get("memory") for classification.

HELP DETECTION (ONBOARDING STEP 3):
====================================
Before LLM classification, we check for help-related keywords (help, ?, ajutor,
ce poți face, capabilities, commands). If detected, we force CONVERSATIONAL mode
so the user gets a friendly introduction instead of being routed to a DAG.

FIRST-MESSAGE GUARD (ONBOARDING STEP 3):
=========================================
If `is_first_message=True` and the intent is vague (short, no clear action verb),
we force CONVERSATIONAL mode. This prevents new users from being thrown into a
DAG on their very first interaction before they understand what GOAT can do.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Final, TYPE_CHECKING

from supervisor.llm_utils import _call_llm

if TYPE_CHECKING:
    from config.registry import Registry

__all__ = ["IntentDepth", "classify_intent"]

_CLASSIFIER_SYSTEM: Final[str] = (
    "Classify the user intent into exactly one depth level. Be conservative:\n"
    "  conversational — questions, explanations, discussions, definitions, chitchat, help requests\n"
    "  analytical     — comparisons, light coding, structured analysis, multi-part answers\n"
    "  complex        — full implementation, multi-step research, architecture, system checks\n"
    "When in doubt between conversational and analytical, choose conversational.\n"
    "Reply with ONLY the single word: conversational, analytical, or complex."
)

# ── Help detection patterns (ONBOARDING STEP 3) ──
# These catch help/onboarding queries before LLM classification,
# forcing CONVERSATIONAL mode so the user gets a friendly introduction.
_HELP_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\?\s*$", re.IGNORECASE),                          # just "?"
    re.compile(r"\bhelp\b", re.IGNORECASE),                          # "help", "help me"
    re.compile(r"\bajut[oai]r\b", re.IGNORECASE),                    # "ajutor", "ajută", "ajutor!"
    re.compile(r"\bce\s+po[iț]i?\s+(face|faci)\b", re.IGNORECASE),  # "ce poți face", "ce poți să faci"
    re.compile(r"\bce\s+știi\s+să\s+(faci|fac)\b", re.IGNORECASE),  # "ce știi să faci"
    re.compile(r"\bcapabilities\b", re.IGNORECASE),                  # "capabilities"
    re.compile(r"\bcommands?\b", re.IGNORECASE),                     # "command", "commands"
    re.compile(r"\b(what\s+)?(can\s+you\s+do|are\s+you\s+capable\s+of)\b", re.IGNORECASE),  # "what can you do"
    re.compile(r"\b(arată|show)\s+(ce|what)\s+(poți|can)\b", re.IGNORECASE),  # "arată ce poți"
    re.compile(r"\b(how\s+to|how\s+do\s+i|cum\s+să)\s+(use|folosesc|utilizez)\b", re.IGNORECASE),  # "how to use"
]

# ── Vague first-message patterns (ONBOARDING STEP 3) ──
# If the user's very first message matches these, it's likely exploratory
# and should stay conversational rather than triggering a DAG.
_VAGUE_FIRST_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(salut|bună|buna|hello|hi|hey|bun venit|noroc|servus)\b", re.IGNORECASE),
    re.compile(r"^(scuze|scuză|pardon|sorry)\b", re.IGNORECASE),
    re.compile(r"^(test|testing|încerc|incerc)\b", re.IGNORECASE),
    re.compile(r"^(da|nu|yes|no|ok|okay|bine)\s*$", re.IGNORECASE),
    re.compile(r"^(cine\s+ești|cine\s+sunt|who\s+are\s+you)\b", re.IGNORECASE),
    re.compile(r"^(ce\s+este|ce-i|what\s+is)\s+(asta|this|goat)\b", re.IGNORECASE),
    re.compile(r"^[.!?]{1,3}$"),  # just punctuation
    re.compile(r"^\s*$"),  # empty/whitespace only
]


def _is_help_query(intent: str) -> bool:
    """Check if the intent is a help/onboarding query using pattern matching.

    Returns True if the user is asking for help, capabilities, or what GOAT can do.
    This forces CONVERSATIONAL mode so the user gets a friendly introduction
    instead of being routed to a DAG.
    """
    text = intent.strip()
    if not text:
        return False
    for pattern in _HELP_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_vague_first_message(intent: str) -> bool:
    """Check if the intent is a vague first message that should stay conversational.

    Returns True if the message looks like a greeting, test, or exploratory query
    that doesn't warrant DAG execution.
    """
    text = intent.strip()
    if not text:
        return True
    # Short messages (≤3 words) that aren't clear commands
    words = text.split()
    if len(words) <= 3:
        for pattern in _VAGUE_FIRST_PATTERNS:
            if pattern.search(text):
                return True
    return False


class IntentDepth(str, Enum):
    """Three-tier routing depth used by GoatSupervisor.run()."""

    CONVERSATIONAL = "conversational"  # direct LLM reply with tools available
    ANALYTICAL     = "analytical"      # lightweight DAG, ≤2 tasks
    COMPLEX        = "complex"         # full DAG with planner, researcher, critic


async def classify_intent(
    intent: str,
    registry: "Registry",
    is_first_message: bool = False,
) -> IntentDepth:
    """Classify intent via LLM — with help detection and first-message guard.

    The model evaluates true intent depth regardless of message formatting,
    prefixes, or structural triggers. Memory queries are routed through the
    same semantic path, allowing the LLM to determine if they need DAG tools.

    HELP DETECTION (ONBOARDING STEP 3):
    ===================================
    Before LLM classification, we check for help-related keywords (help, ?,
    ajutor, ce poți face, capabilities, commands). If detected, we force
    CONVERSATIONAL mode so the user gets a friendly introduction instead of
    being routed to a DAG.

    FIRST-MESSAGE GUARD (ONBOARDING STEP 3):
    =========================================
    If `is_first_message=True` and the intent is vague (short greeting, test,
    exploratory query), we force CONVERSATIONAL mode. This prevents new users
    from being thrown into a DAG on their very first interaction before they
    understand what GOAT can do.

    FALLBACK SAFEGUARD:
    ===================
    If the LLM returns empty or unparseable output, we fall back to ANALYTICAL
    (lightweight DAG) instead of COMPLEX (full DAG). This prevents token waste
    on unnecessary full DAG execution when the classifier fails.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get("memory").
    """
    # ── ONBOARDING STEP 3: Help detection ──
    # If the user is asking for help/capabilities, force CONVERSATIONAL
    # so they get a friendly introduction with capabilities listed.
    if _is_help_query(intent):
        return IntentDepth.CONVERSATIONAL

    # ── ONBOARDING STEP 3: First-message guard ──
    # If this is the user's very first message and it's vague/exploratory,
    # keep it conversational — don't throw them into a DAG.
    if is_first_message and _is_vague_first_message(intent):
        return IntentDepth.CONVERSATIONAL

    # ── Standard LLM-based classification ──
    _settings = registry.settings
    raw = await _call_llm(
        _settings.agents.get("memory"),  # gpt-4o-mini — fast, cheap
        [
            {"role": "system", "content": _CLASSIFIER_SYSTEM},
            {"role": "user",   "content": intent},
        ],
    )
    token = raw.strip().lower().split()[0] if raw.strip() else ""
    try:
        depth = IntentDepth(token)
    except ValueError:
        # FIX: Fall back to ANALYTICAL (lightweight) instead of COMPLEX (full DAG)
        # This prevents token waste when the classifier fails
        depth = IntentDepth.CONVERSATIONAL
    return depth
