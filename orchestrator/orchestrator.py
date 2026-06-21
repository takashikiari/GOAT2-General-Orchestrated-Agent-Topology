"""
orchestrator.orchestrator — LLM chatbot with Redis-backed conversation state.

Orchestrator uses the registry's working_memory directly as the single source
of truth for conversation state.  No in-memory caching layer — every turn
reads from and writes to Redis so state is consistent across restarts and
requires no secondary synchronisation.

Usage:
    registry = ServiceRegistry()
    o = Orchestrator(registry)
    reply = await o.run("Hello", "chat-123")
    reply = await o.run("What did I say?", "chat-123")
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config import settings

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

_SYSTEM_PROMPT = "You are a helpful assistant."


class Orchestrator:
    """
    Stateless chatbot driver: (intent, chat_id) → Redis → LLM → Redis → text.

    All conversation state lives in working_memory keyed by chat_id.  The
    orchestrator holds no state itself, so one instance safely handles many
    concurrent sessions.
    """

    def __init__(self, registry: ServiceRegistry) -> None:
        """
        Args:
            registry: ServiceRegistry providing the LLM client and working memory.
        """
        self._registry = registry

    async def run(self, intent: str, chat_id: str) -> str:
        """
        Load history, call LLM, persist updated history, return reply.

        Steps:
            1. Load prior messages from registry.working_memory for chat_id.
            2. Append the user message.
            3. Call LLM with [system] + messages.
            4. Append the assistant response.
            5. Save the full updated list back to working_memory.
            6. Return the response text.

        Args:
            intent:  User's request in plain text.
            chat_id: Stable identifier for this conversation (e.g. Telegram chat id
                     as a string).  Used as the storage key in working_memory.

        Returns:
            LLM response text, or an empty string if the model returned none.
        """
        messages = await self._registry.working_memory.get_messages(chat_id)
        messages.append({"role": "user", "content": intent})

        api_messages = [{"role": "system", "content": _SYSTEM_PROMPT}, *messages]
        response = await self._registry.llm_client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=api_messages,
            temperature=settings.TEMPERATURE,
            max_tokens=settings.MAX_TOKENS,
        )
        reply = response.choices[0].message.content or ""

        messages.append({"role": "assistant", "content": reply})
        await self._registry.working_memory.save_messages(chat_id, messages)
        return reply
