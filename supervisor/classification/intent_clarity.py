"""Intent-clarity analysis — detect when a turn is missing
information the LLM needs to act. Pure Python, no LLM, no regex.

USAGE:
    from supervisor.classification.intent_clarity import (
        build_clarity_context, missing_slots,
    )

    ctx = build_clarity_context(dialogue_text, mem_ctx)
    missing = missing_slots(intent_text)

The two helpers:
  - ``missing_slots(intent)``: pure-Python slot detection. Returns
    a list of slot names (``"path"``, ``"format"``, ``"scope"``,
    ``"name"``) that the intent doesn't mention. Slug patterns
    are pure substring searches — no regex.
  - ``build_clarity_context(dialogue, mem_ctx)``: render a
    ``[Intent Clarity]`` block the LLM can scan. Returns ``""``
    when everything is clear (no missing slots).

Defensive: missing inputs → ``""`` / ``[]``. Never raises.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "SLOT_KEYWORDS",
    "missing_slots",
    "build_clarity_context",
]

# Slot names and the keywords that signal the slot is filled.
# Pure substring search (case-folded). No regex.
SLOT_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "path":   ("path", "file", "directory", "folder", "where",
               "calea", "fisier", "director"),
    "format": ("format", "json", "yaml", "csv", "markdown",
               "xml", "html", "text", "tabel"),
    "scope":  ("scope", "all", "every", "each", "toate", "fiecare",
               "tot", "doar", "only", "just"),
    "name":   ("named", "called", "name", "nume", "titlu"),
}


def missing_slots(intent: str) -> list[str]:
    """Return slot names whose keywords are NOT in ``intent``.

    Args:
        intent: The raw user intent (any case, any language).

    Returns:
        List of slot names whose keywords were not found in the
        intent. Empty list when all slots are filled (or when
        intent is empty / non-string).
    """
    if not intent or not isinstance(intent, str):
        return []
    text = intent.lower()
    missing: list[str] = []
    for slot, kws in SLOT_KEYWORDS.items():
        if not any(kw in text for kw in kws):
            missing.append(slot)
    return missing


def build_clarity_context(dialogue: str, mem_ctx: str) -> str:
    """Render the ``[Intent Clarity]`` block, or ``""`` when clear.

    Args:
        dialogue: Recent dialogue text (from
            ``classification.classifier_prompt.format_dialogue``).
        mem_ctx: The current memory context block.

    Returns:
        A ``"[Intent Clarity]\\n- missing slot: X\\n..."`` block,
        or ``""`` when nothing is missing.
    """
    intent_text = dialogue or mem_ctx or ""
    if not intent_text:
        return ""
    missing = missing_slots(intent_text)
    if not missing:
        return ""
    lines = ["[Intent Clarity]"]
    for slot in missing:
        lines.append(f"- missing slot: {slot}")
    return "\n".join(lines)
