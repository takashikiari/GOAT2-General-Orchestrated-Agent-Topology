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

# Guidance appended to the system prompt when the search_memory tool is
# configured, so GOAT knows to fetch L3 on demand rather than claim it doesn't
# recall something. A constant (content, not a tunable), like _BASE_IDENTITY.
_SEARCH_MEMORY_GUIDANCE = (
    "If the user references something not visible in the conversation above, "
    "use search_memory before saying you don't recall it."
)


class Orchestrator:
    """Stateless (intent, chat_id)→LLM→reply driver.

    All memory access flows through ``registry.memory_layers`` (the Backend
    Mapper). The Orchestrator never imports or references the physical tiers
    (WorkingMemory/EpisodicMemory/PermanentMemory) directly. Context for the
    prompt is assembled and budgeted by ``memory_layers.prepare_context_for_prompt``.

    L3 (episodic) is NOT auto-retrieved: the orchestrator assembles only
    L0+L1+L2 and lets GOAT decide, organically in its one call, whether to
    fetch L3 via the ``search_memory`` tool. (Automatic/prefetch retrieval is
    a separate future step.)
    """

    def __init__(self, registry: ServiceRegistry, tools: list[ToolDefinition] | None = None) -> None:
        self._registry = registry
        self._tools = tools or []

    def _has_search_memory(self) -> bool:
        """True when the ``search_memory`` tool is configured for this orchestrator."""
        return any(t.name == "search_memory" for t in self._tools)

    async def run(self, intent: str, chat_id: str) -> str:
        """Single turn: assemble budgeted L0+L1+L2 context → LLM (one tool round) → save → return text.

        Flow:
            1. ``memory_layers.prepare_context_for_prompt(chat_id)`` assembles
               L0+L1 (identity + facts, always kept) and L2 (history) into
               text blocks whose combined size stays under ``MAX_CONTEXT_TOKENS``.
               L3 is NOT included — GOAT fetches it on demand via search_memory.
            2. Join the blocks into the system prompt; append the search_memory
               guidance when that tool is configured; append the user message.
            3. Call the LLM (with tools if configured).
            4. Handle a single tool round if the model requests it — e.g. a
               search_memory call whose results inform the final answer.
            5. Persist this turn (user + assistant) to working memory (L2).
            6. Return the response text.

        If the model requests tool calls, executes each handler and makes one
        more LLM call (without tools) to produce the final text answer — that
        second call never receives tools, so runaway tool-calling loops are
        structurally impossible. Multi-round support is a separate future step.
        """
        layers = self._registry.memory_layers
        context_blocks = await layers.prepare_context_for_prompt(chat_id)
        system_content = "\n\n".join(context_blocks)
        if self._has_search_memory():
            system_content += f"\n\n{_SEARCH_MEMORY_GUIDANCE}"
        api_msgs = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": intent},
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
        # Persist this turn: reload history, append user + assistant, save (L2).
        messages = await layers.get_working_context(chat_id)
        messages.append({"role": "user", "content": intent, "timestamp": time.time()})
        messages.append({"role": "assistant", "content": reply, "timestamp": time.time()})
        await layers.save_working_context(chat_id, messages)
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
