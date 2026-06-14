"""ClarityContext — pure context builder (NO LLM) for the GOAT decision call.

Part of the single-call architecture: this module no longer judges clarity (the
old ``check_intent_clarity`` LLM call is gone). It just packages the conversation
and memory context so the one GOAT decision call can judge clarity itself.

``missing_info`` is intentionally **structural only** — it never matches keywords
or applies heuristics about the message text (no regex, no hardcoded rules). It
notes facts like "no prior conversation" / "no memory context" so GOAT knows what
grounding it has; GOAT decides whether to ask for clarification.
"""
from __future__ import annotations

import dataclasses
import logging

log = logging.getLogger("goat2.supervisor.pipeline.intent_clarity")

__all__ = ["ClarityContext", "build_clarity_context"]

_NO_HISTORY_MARKERS = ("(no prior conversation)", "")


@dataclasses.dataclass
class ClarityContext:
    """Conversation/memory grounding for GOAT's clarity judgment (pure context).

    Attributes:
        history_text: Formatted recent conversation (both speakers) or a marker.
        has_history: Whether any prior conversation exists.
        mem_ctx: Pre-computed memory context for this turn.
        has_memory: Whether any memory context is present.
        missing_info: Structural grounding gaps (e.g. "no prior conversation") —
            no keyword/regex analysis of the message; GOAT decides on clarity.
    """

    history_text: str
    has_history: bool
    mem_ctx: str
    has_memory: bool
    missing_info: list[str]

    def to_prompt(self) -> str:
        """Render this context as a prompt block for the GOAT decision call."""
        lines = ["[Conversation context]"]
        lines.append(self.history_text if self.has_history else "(no prior conversation)")
        if self.missing_info:
            lines.append("Grounding gaps: " + "; ".join(self.missing_info))
        return "\n".join(lines)


def build_clarity_context(history_text: str, mem_ctx: str) -> ClarityContext:
    """Assemble the ClarityContext for this turn — pure, no LLM.

    Args:
        history_text: Formatted recent conversation (e.g. from ``format_dialogue``).
        mem_ctx: Pre-computed memory context string.

    Returns:
        A populated ClarityContext with structural ``missing_info`` only.
    """
    has_history = bool(history_text) and history_text.strip() not in _NO_HISTORY_MARKERS
    has_memory = bool(mem_ctx and mem_ctx.strip())
    missing: list[str] = []
    if not has_history:
        missing.append("no prior conversation")
    if not has_memory:
        missing.append("no memory context")
    log.debug("build_clarity_context: has_history=%s has_memory=%s", has_history, has_memory)
    return ClarityContext(
        history_text=history_text or "(no prior conversation)",
        has_history=has_history,
        mem_ctx=mem_ctx or "",
        has_memory=has_memory,
        missing_info=missing,
    )
