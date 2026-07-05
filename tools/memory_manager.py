"""tools.memory_manager — read_l1, forget_fact, memory_status tools.

read_l1    — show all L1 facts with token usage
forget_fact — delete a specific key from L1
memory_status — entry counts for L1, L2, L3
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from memory.budget import estimate_tokens
from memory.config import L1_FACTS_MAX_TOKENS
from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)

__all__ = ["build_memory_manager_tools"]


def _fmt_facts(facts: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in facts.items())


def build_memory_manager_tools(memory_layers: "MemoryLayers") -> list[ToolDefinition]:
    """Build read_l1, forget_fact, and memory_status tools."""

    async def read_l1_handler(chat_id: str = "") -> str:
        facts = await memory_layers.get_l1_facts()
        if not facts:
            return "L1 is empty — no permanent facts stored yet."
        formatted = _fmt_facts(facts)
        used = estimate_tokens(formatted)
        return (
            f"L1 facts ({len(facts)} entries, {used}/{L1_FACTS_MAX_TOKENS} tokens):\n"
            + formatted
        )

    async def forget_fact_handler(key: str, chat_id: str = "") -> str:
        existed = await memory_layers.delete_l1_fact(key)
        if existed:
            log.info("forget_fact chat=%s key=%r deleted", chat_id, key)
            return f"✅ Forgotten: '{key}' removed from L1."
        return f"❌ Key '{key}' not found in L1 — nothing deleted."

    async def memory_status_handler(chat_id: str = "") -> str:
        counts = await memory_layers.get_layer_counts(chat_id)
        facts = await memory_layers.get_l1_facts()
        used = estimate_tokens(_fmt_facts(facts)) if facts else 0
        return (
            f"Memory status:\n"
            f"  L1 (permanent facts): {counts['l1_facts']} entries"
            f" — {used}/{L1_FACTS_MAX_TOKENS} tokens used\n"
            f"  L2 (working memory):  {counts['l2_messages']} messages\n"
            f"  L3 (episodic):        {counts['l3_this_chat']} entries this chat"
            f" / {counts['l3_total']} total"
        )

    return [
        ToolDefinition(
            name="read_l1",
            description=(
                "Read all permanent facts currently stored in L1 core-memory, "
                "including token usage vs cap. Call this before promoting a new "
                "fact to check what's already there, or when the user asks what "
                "you remember permanently about them."
            ),
            parameters={"type": "object", "properties": {}},
            handler=read_l1_handler,
        ),
        ToolDefinition(
            name="forget_fact",
            description=(
                "Delete a permanent fact from L1 by its key. Use this when the "
                "user asks you to forget something, when a fact is outdated, or "
                "to make room in L1 before promoting a new fact. The key must "
                "exactly match the key shown by read_l1."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The exact L1 fact key to delete (e.g. 'user_name')",
                    },
                },
                "required": ["key"],
            },
            handler=forget_fact_handler,
        ),
        ToolDefinition(
            name="memory_status",
            description=(
                "Show entry counts for all memory layers: L1 (permanent facts + "
                "token usage), L2 (working messages in this chat), L3 (episodic "
                "entries for this chat and globally). Call this when the user "
                "asks about memory capacity, how much you remember, or memory health."
            ),
            parameters={"type": "object", "properties": {}},
            handler=memory_status_handler,
        ),
    ]
