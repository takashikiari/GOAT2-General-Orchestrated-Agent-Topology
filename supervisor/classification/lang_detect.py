"""Detect the dominant natural language of a user intent via LLM.

REGISTRY INJECTION (PHASE 4):
=============================
detect_language() now requires `registry` parameter.
Uses registry.settings.agents.get() for model access.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Final

from utils.llm_utils import _call_llm

if TYPE_CHECKING:
    from config.registry import Registry

__all__ = ["detect_language"]

_SYSTEM: Final[str] = (
    "Identify the natural language of the user message. "
    "Reply with ONLY the language name in English (e.g. English, Romanian, French, Spanish). "
    "If the message is mixed, name the dominant language."
)


async def detect_language(intent: str, registry: "Registry") -> str:
    """
    Return the dominant language of intent as an English name; falls back to 'English'.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get() for model access.
    """
    if not intent.strip():
        return "English"
    try:
        raw = await _call_llm(
            registry.settings.agents.get("memory"),  # gpt-4o-mini — fast, cheap
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": intent[:500]},
            ],
        )
        lang = raw.strip().split()[0].rstrip(".,;:")
        return lang if lang else "English"
    except Exception:
        return "English"
