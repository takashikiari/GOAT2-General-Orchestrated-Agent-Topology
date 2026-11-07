"""orchestrator.llm_response — read the first choice of a chat completion without crashing.

Some OpenAI-compatible providers answer HTTP 200 with a body of `null` (the SDK then returns
None) or with an error object and no `choices` (the SDK returns a ChatCompletion whose choices
is None). Indexing `.choices[0]` on either crashed the whole turn (2026-11-07 09:51:58, after a
203 s tool-round call). `first_choice` turns both into an empty choice, so the existing
empty-reply handling (retry / "empty" log) runs instead of an exception.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from utils.logging.setup import get_logger

log = get_logger(__name__)
__all__ = ["first_choice", "NO_CHOICES"]

NO_CHOICES = "no_choices"


def first_choice(response: Any) -> Any:
    """`response.choices[0]`, or an empty choice (content "", no tool calls) if there is none."""
    choices = getattr(response, "choices", None) if response is not None else None
    if choices:
        return choices[0]
    detail = "None" if response is None else repr(getattr(response, "model_extra", None) or response)[:300]
    log.warning("LLM response without choices (provider returned %s) — treating as empty reply", detail)
    return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None, role="assistant"),
                           finish_reason=NO_CHOICES)
