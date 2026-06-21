"""orchestrator.orchestrator — stateless LLM driver with optional single-round tool calling."""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from config import settings
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from orchestrator.tools import ToolDefinition
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
_SYSTEM_PROMPT = "You are a helpful assistant."


class Orchestrator:
    """Stateless (intent, chat_id)→LLM→reply driver. All state lives in working_memory."""

    def __init__(self, registry: ServiceRegistry, tools: list[ToolDefinition] | None = None) -> None:
        self._registry = registry
        self._tools = tools or []

    async def run(self, intent: str, chat_id: str) -> str:
        """Single turn: load history → LLM (one tool round if requested) → save → return text.

        If the model requests tool calls, executes each handler and makes one
        more LLM call (without tools) to produce the final text answer.  That
        second call never receives tools, so runaway tool-calling loops are
        structurally impossible.  Multi-round support is a separate future step.
        """
        messages = await self._registry.working_memory.get_messages(chat_id)
        messages.append({"role": "user", "content": intent, "timestamp": time.time()})
        api_msgs = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            *({"role": m["role"], "content": m["content"]} for m in messages),
        ]
        kw: dict = dict(model=settings.MODEL_NAME, messages=api_msgs,
                        temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS)
        if self._tools:
            kw["tools"] = [t.to_openai_schema() for t in self._tools]
            kw["tool_choice"] = "auto"
        response = await self._registry.llm_client.chat.completions.create(**kw)
        choice = response.choices[0]
        if choice.message.tool_calls:
            reply = await self._run_tool_round(api_msgs, choice)
        else:
            reply = choice.message.content or ""
        messages.append({"role": "assistant", "content": reply, "timestamp": time.time()})
        await self._registry.working_memory.save_messages(chat_id, messages)
        return reply

    async def _run_tool_round(self, api_msgs: list, choice) -> str:
        """Execute tool calls from choice, make one more LLM call (no tools), return text."""
        tool_exchange = [
            {"role": "assistant", "content": choice.message.content,
             "tool_calls": [
                 {"id": tc.id, "type": "function",
                  "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                 for tc in choice.message.tool_calls
             ]}
        ]
        for tc in choice.message.tool_calls:
            result = await self._call_tool(tc)
            tool_exchange.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        r2 = await self._registry.llm_client.chat.completions.create(
            model=settings.MODEL_NAME, messages=api_msgs + tool_exchange,
            temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS,
        )
        return r2.choices[0].message.content or ""

    async def _call_tool(self, tc) -> str:
        """Dispatch a tool call. Returns str(result) or JSON {"error": ...} on failure."""
        handler = next((t for t in self._tools if t.name == tc.function.name), None)
        if handler is None:
            return json.dumps({"error": f"unknown tool: {tc.function.name}"})
        try:
            args = json.loads(tc.function.arguments)
            return str(await handler.handler(**args))
        except Exception as exc:
            log.warning("Tool %s raised: %s", tc.function.name, exc)
            return json.dumps({"error": str(exc)})
