"""Intent clarity check — GOAT asks LLM whether an intent is clear enough for DAG execution.

A single LLM call returns "clear" or "unclear". If unclear, GoatSupervisor returns a
conversational clarification request instead of spawning the DAG.

All ambiguity judgment is delegated entirely to the LLM — no keyword matching,
no pattern lists, no length heuristics.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from config.timeouts import TURN_TIMEOUT
from utils.llm_utils import _call_llm

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.pipeline.intent_clarity")

__all__ = ["check_intent_clarity"]

_SYSTEM: str = (
    "You are GOAT's intent clarity checker. Your sole job is to decide whether a "
    "user's intent is specific enough for a multi-agent DAG to execute without "
    "further clarification.\n\n"
    "Reply with exactly one word: clear or unclear.\n\n"
    "Consider an intent unclear only when the DAG would need to guess essential "
    "parameters — target file path, desired scope, timeframe, or subject — that "
    "are not answerable from the memory context or conversation history.\n"
    "Reason semantically. Do not pattern-match on syntax or length."
)


async def check_intent_clarity(
    intent: str,
    mem_ctx: str,
    history_text: str,
    registry: "Registry",
) -> bool:
    """Ask the LLM whether the intent is clear enough for DAG execution.

    Returns True (clear) when the DAG can proceed. Defaults to True on any
    failure or unexpected LLM output so ambiguity never hard-blocks the pipeline.

    Args:
        intent: The user's intent text.
        mem_ctx: Pre-computed working-memory context (may resolve ambiguities).
        history_text: Formatted recent conversation history.
        registry: ServiceRegistry for model configuration.

    Returns:
        True if the LLM judges the intent clear; False requests clarification.
    """
    spec = registry.settings.supervisor.model
    user_parts = [f"Intent: {intent}"]
    if mem_ctx:
        user_parts.append(f"\nMemory context (may fill in gaps):\n{mem_ctx}")
    if history_text:
        user_parts.append(f"\nRecent conversation:\n{history_text}")
    user_parts.append("\nIs this intent clear enough to execute?")

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
        verdict = raw.strip().lower().split()[0] if raw.strip() else "clear"
        is_clear = verdict == "clear"
        log.debug("intent_clarity: verdict=%s intent=%.80s", verdict, intent)
        return is_clear
    except Exception as exc:
        log.warning("check_intent_clarity: failed — defaulting to clear: %s", exc)
        return True
