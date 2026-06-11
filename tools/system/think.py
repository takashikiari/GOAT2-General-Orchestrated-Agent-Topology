"""Chain-of-thought reasoning tool — records a private reasoning step.

Provides a single ToolDefinition (THINK) that allows the model to record
internal reasoning before calling other tools. The handler is a pure
pass-through with no I/O side effects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools._make_tool import make_tool

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.system.think")

__all__ = ["THINK"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {
            "type": "string",
            "description": "The reasoning step to record before taking action.",
        },
    },
    "required": ["thought"],
}


async def _handler(thought: str) -> str:
    # Pure — no I/O. Returned string appears in tool-call history for the next LLM turn.
    log.debug("think: %d chars", len(thought))
    return thought


THINK = make_tool(
    name="think",
    description=(
        "Record a private reasoning step before acting. "
        "Use for chain-of-thought — the model thinks out loud before calling other tools."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
