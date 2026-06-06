"""Intent depth classifier — routes intents to conversational, analytical, or complex handling."""
from __future__ import annotations

import re
from enum import Enum
from typing import Final

from config.settings import settings
from supervisor.llm_utils import _call_llm

__all__ = ["IntentDepth", "classify_intent", "_is_file_op", "_is_search_intent"]

# Matches first-person status statements regardless of technical vocabulary.
# Three surfaces: contractions (I'm/I've), spaced forms (I am/I have/I just), simple past.
# Excludes cognitive verbs (wondering, thinking, trying) and trailing-? questions.
_ACTION_VERBS: Final[str] = r"working|implementing|building|writing|fixing|testing|developing|refactoring|debugging|deploying|creating|adding|running|migrating|integrating|configuring|updating|upgrading|switching|replacing|pushing|merging|shipping|using"
_PAST_PARTS: Final[str]   = r"finished|completed|built|written|fixed|added|implemented|created|deployed|pushed|merged|run|tested|done|made|released|shipped|migrated"
_STATUS_RE: Final[re.Pattern[str]] = re.compile(
    rf"^(?:i'm\s+(?:{_ACTION_VERBS})|i've\s+(?:{_PAST_PARTS})"
    rf"|i\s+am\s+(?:{_ACTION_VERBS})|i\s+have\s+(?:{_PAST_PARTS})"
    rf"|i\s+just\s+\w+|i\s+(?:started|{_PAST_PARTS}))\b",
    re.IGNORECASE,
)
_SEARCH_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:search|look\s*up|lookup|google|browse|web\s*search|find\s+online|internet|online)\b"
    r"|\bcaut[aă]\w*\b|\bnet\b",
    re.IGNORECASE,
)

_CLASSIFIER_SYSTEM: Final[str] = (
    "Classify the user intent into exactly one depth level:\n"
    "  conversational — greetings, chitchat, status updates (\"I'm working on X\","
    " \"I just did Y\"), quick definitions, simple yes/no\n"
    "  analytical     — explain concepts, compare options, light coding, structured analysis\n"
    "  complex        — multi-step research, full implementation, architecture design\n"
    "Reply with ONLY the single word: conversational, analytical, or complex."
)


class IntentDepth(str, Enum):
    """Three-tier routing depth used by GoatSupervisor.run()."""

    CONVERSATIONAL = "conversational"  # direct LLM reply, no DAG
    ANALYTICAL     = "analytical"      # lightweight DAG, ≤2 tasks, no researcher
    COMPLEX        = "complex"         # full DAG with planner, researcher, critic


_FILE_OP_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:create|write|read|delete|remove|save|edit)\b.{0,40}\bfile\b"
    r"|\bfile\b.{0,40}\b(?:create|write|read|delete|remove|save|edit)\b"
    r"|(?:~/|/home/|/tmp/|/var/|/etc/)\S+"
    r"|\bfi[sşș]ier\w*\b",          # Romanian: fișier / fisier / fișierul
    re.IGNORECASE,
)


def _is_file_op(intent: str) -> bool:
    """True when intent explicitly requests a file operation. Pure — PyO3 candidate."""
    return bool(_FILE_OP_RE.search(intent))

def _is_search_intent(intent: str) -> bool:
    """True when intent contains web-search keywords. Pure — PyO3 candidate."""
    return bool(_SEARCH_RE.search(intent))

def _is_status_update(intent: str) -> bool:
    """True for first-person status statements; short-circuits before the LLM. Pure."""
    s = intent.strip()
    return bool(_STATUS_RE.match(s)) and not s.endswith("?")


async def classify_intent(intent: str) -> IntentDepth:
    """Classify intent; short-circuits to CONVERSATIONAL for status updates before LLM."""
    if _is_status_update(intent):
        return IntentDepth.CONVERSATIONAL
    raw = await _call_llm(
        settings.agents.get("memory"),  # gpt-4o-mini — fast, cheap
        [
            {"role": "system", "content": _CLASSIFIER_SYSTEM},
            {"role": "user",   "content": intent},
        ],
    )
    token = raw.strip().lower().split()[0] if raw.strip() else ""
    try:
        depth = IntentDepth(token)
    except ValueError:
        depth = IntentDepth.COMPLEX
    if depth == IntentDepth.CONVERSATIONAL and _is_file_op(intent):
        return IntentDepth.ANALYTICAL
    return depth
