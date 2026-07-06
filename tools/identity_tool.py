"""tools.identity_tool — set_identity tool: GOAT's path to update L0 identity.

GOAT calls this when the user explicitly asks it to change its name, persona,
or behaviour at the identity level. The new prompt is stored in Letta and
overrides the config base_prompt on every future turn. Passing an empty string
clears the override and restores the config prompt.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)

__all__ = ["build_set_identity_tool"]


def build_set_identity_tool(memory_layers: "MemoryLayers") -> ToolDefinition:
    """Build the set_identity tool, bound to a ``MemoryLayers`` instance."""

    async def handler(identity_prompt: str, chat_id: str = "") -> str:
        if identity_prompt.strip() == "":
            try:
                await memory_layers.set_identity_override("")
                log.info("set_identity: cleared override chat=%s", chat_id)
                return "✅ Identity reset to default (config base_prompt)."
            except Exception as exc:
                return f"❌ set_identity failed (Letta unavailable): {exc}"
        try:
            await memory_layers.set_identity_override(identity_prompt.strip())
            log.info("set_identity: updated chat=%s (%d chars)", chat_id, len(identity_prompt))
            return f"✅ Identity updated ({len(identity_prompt.strip())} chars). Takes effect next turn."
        except Exception as exc:
            return f"❌ set_identity failed (Letta unavailable): {exc}"

    return ToolDefinition(
        name="set_identity",
        description=(
            "Update GOAT's core identity prompt (L0) stored in permanent memory. "
            "Use this when the user explicitly asks you to change your name, persona, "
            "or fundamental behaviour — e.g. 'from now on call yourself Max' or "
            "'always respond in Romanian'. The new prompt overrides the config default "
            "and persists across all future sessions. Pass an empty string to reset "
            "to the default config prompt."
        ),
        parameters={
            "type": "object",
            "properties": {
                "identity_prompt": {
                    "type": "string",
                    "description": (
                        "The full identity prompt to use from now on. "
                        "Pass an empty string to reset to the default."
                    ),
                },
            },
            "required": ["identity_prompt"],
        },
        handler=handler,
    )
