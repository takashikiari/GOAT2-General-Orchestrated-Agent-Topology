"""
GOAT 2.0 — BaseAgent

Abstract base class for all GOAT agents.

Subclass and implement execute(); get tool calling, context formatting, and
supervisor compatibility for free:

    class ResearchAgent(BaseAgent):
        role = "researcher"

        async def execute(self, task: AgentTask, context: dict[str, AgentResult]) -> str:
            messages = self._build_messages(task, context)
            return await self._chat(messages)

    # With inline tools:
    class WebAgent(BaseAgent):
        role = "web"

        @tool(
            name="fetch_url",
            description="Fetch the text content of a URL",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "The URL to fetch"}},
                "required": ["url"],
            },
        )
        async def _fetch(self, url: str) -> str:
            async with httpx.AsyncClient() as c:
                r = await c.get(url, timeout=15)
                return r.text[:4000]

        async def execute(self, task, context):
            return await self._chat(self._build_messages(task, context))

Register an instance as an AgentRunner (instances are directly callable):
    supervisor.register_agent("researcher", ResearchAgent(spec))
    supervisor.register_agent("web", WebAgent(spec, MY_SYSTEM_PROMPT))
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import AsyncOpenAI

from config.settings import ModelSpec, Provider, PROVIDER_BASE_URLS, Settings
from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agent")

__all__ = ["BaseAgent", "ToolDefinition", "tool"]


# ---------------------------------------------------------------------------
# Tool primitives
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """
    Describes one callable tool the LLM may invoke.

    `parameters` must be a JSON Schema object describing the function
    arguments — the same shape OpenAI's function calling API expects.
    `handler` is called with **kwargs matching that schema; may be sync
    or async.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> Callable:
    """
    Class-method decorator that marks a BaseAgent method as a tool.
    Tools are auto-discovered and registered when the agent is instantiated.

    Example:
        @tool("add", "Add two integers", {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        })
        async def _add(self, a: int, b: int) -> int:
            return a + b
    """
    def decorator(func: Callable) -> Callable:
        func._tool_meta = (name, description, parameters)
        return func
    return decorator


# ---------------------------------------------------------------------------
# LLM client cache — independent from supervisor's module-level cache
# ---------------------------------------------------------------------------

_clients: dict[str, AsyncOpenAI] = {}


def _get_client(spec: ModelSpec) -> AsyncOpenAI:
    key = spec.provider.value
    if key not in _clients:
        # Import Settings on demand to avoid circular imports
        from config.settings import Settings
        _clients[key] = AsyncOpenAI(
            api_key=Settings().api_keys.for_provider(spec.provider),
            base_url=PROVIDER_BASE_URLS[key],
        )
    return _clients[key]


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """
    Abstract base for all GOAT 2.0 agents.

    Every subclass must:
      - Set a class-level `role` string (used for logging and registration).
      - Implement `async execute(task, context) -> str`.

    Every subclass gets:
      - `_chat(messages)` — LLM call with a full agentic tool-calling loop.
      - `_build_messages(task, context)` — standard [system, user] builder.
      - `_format_context(context)` — formats upstream results as a context block.
      - `add_tool(...)` / `remove_tool(...)` — dynamic tool registration.
      - `__call__` — makes instances usable as supervisor AgentRunner callables.
    """

    role: str = "base"

    def __init__(
        self,
        spec: ModelSpec,
        system_prompt: str,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.4,
        max_tool_rounds: int = 10,
    ) -> None:
        """
        Args:
            spec:            ModelSpec from config.settings (provider + model_id).
            system_prompt:   Default system prompt for this agent's LLM calls.
            tools:           Initial tool list; supplemented by @tool-decorated methods.
            temperature:     Default sampling temperature.
            max_tool_rounds: Max LLM ↔ tool call iterations before forcing a text reply.
        """
        self.spec = spec
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tool_rounds = max_tool_rounds

        self._tools: dict[str, ToolDefinition] = {}

        # 1. Register tools passed directly to __init__
        for td in (tools or []):
            self._tools[td.name] = td

        # 2. Auto-discover @tool-decorated methods on the concrete subclass.
        #    Using type(self) ensures we walk the MRO but bind to this instance.
        for attr_name in dir(type(self)):
            method = getattr(type(self), attr_name, None)
            if callable(method) and hasattr(method, "_tool_meta"):
                t_name, t_desc, t_params = method._tool_meta
                self._tools[t_name] = ToolDefinition(
                    name=t_name,
                    description=t_desc,
                    parameters=t_params,
                    handler=getattr(self, attr_name),  # bound to self
                )

        log.debug("%r ready  tools=%s", self, list(self._tools))

    # ------------------------------------------------------------------
    # Abstract interface — subclasses implement this
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """
        Run the agent for one task turn.

        Args:
            task:    The AgentTask assigned to this GOAT agent.
            context: Outputs of all upstream dependency tasks keyed by task_id.

        Returns:
            Agent output string — passed to downstream agents and the supervisor.
        """

    # ------------------------------------------------------------------
    # AgentRunner protocol — instances are directly usable by the supervisor
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """Delegate to execute(); makes every instance a valid AgentRunner."""
        return await self.execute(task, context)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def add_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        """Dynamically register a tool. Overwrites any existing tool with the same name."""
        self._tools[name] = ToolDefinition(name, description, parameters, handler)
        log.debug("%r: added tool %r", self, name)

    def remove_tool(self, name: str) -> None:
        """Deregister a tool by name. No-op if the tool doesn't exist."""
        self._tools.pop(name, None)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    # ------------------------------------------------------------------
    # LLM chat with agentic tool-calling loop
    # ------------------------------------------------------------------

    async def _chat(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tool_rounds: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> str:
        """
        Send messages to the LLM and handle tool calls until the model returns
        a plain text response (or the tool round limit is reached).

        Args:
            messages:        Initial message list. Not mutated — a copy is used.
            temperature:     Override instance temperature for this call only.
            json_mode:       Request JSON output. Effective on OpenAI; on other
                             providers you must also ask for JSON in the prompt.
            max_tool_rounds: Override instance max_tool_rounds for this call.
            tools:           Override the instance tool registry for this call.
                             Pass [] to disable tool calling for this call.

        Returns:
            Final text content from the model.

        Note:
            Tool calling is only supported on models that accept the `tools`
            parameter (OpenAI, Groq llama-3.x, DeepSeek deepseek-chat).
            deepseek-reasoner (R1) does not support tool calling.
        """
        client   = _get_client(self.spec)
        temp     = temperature if temperature is not None else self.temperature
        rounds   = max_tool_rounds if max_tool_rounds is not None else self.max_tool_rounds
        tool_map = (
            {td.name: td for td in tools}
            if tools is not None
            else dict(self._tools)
        )
        schema = [td.to_openai() for td in tool_map.values()]

        history = list(messages)  # never mutate the caller's list

        for round_idx in range(rounds + 1):
            use_tools = bool(schema) and round_idx < rounds

            kwargs: dict[str, Any] = {
                "model":       self.spec.model_id,
                "messages":    history,
                "temperature": temp,
            }
            if use_tools:
                kwargs["tools"]       = schema
                kwargs["tool_choice"] = "auto"
            if json_mode and not use_tools and self.spec.provider == Provider.OPENAI:
                kwargs["response_format"] = {"type": "json_object"}

            resp = await client.chat.completions.create(**kwargs)
            msg  = resp.choices[0].message

            # No tool calls → model is done; return the text content.
            if not msg.tool_calls:
                return msg.content or ""

            # --- Tool-call round ---
            log.debug(
                "%r: tool round %d/%d — calls: %s",
                self, round_idx + 1, rounds,
                [tc.function.name for tc in msg.tool_calls],
            )

            # Append the assistant's tool-call turn.
            # content may be None when only tool_calls are present.
            assistant_entry: dict[str, Any] = {
                "role":       "assistant",
                "content":    msg.content,
                "tool_calls": [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            history.append(assistant_entry)

            # Dispatch each call and append the tool result.
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    args = {}
                    log.warning("%r: bad tool arguments for %r: %s", self, tc.function.name, exc)

                result = await self._dispatch_tool(tc.function.name, args, tool_map)
                history.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result),
                })

        # Every round returned tool_calls — force a plain text reply.
        log.warning(
            "%r: exhausted %d tool rounds; forcing final text answer", self, rounds
        )
        resp = await client.chat.completions.create(
            model=self.spec.model_id,
            messages=history,
            temperature=temp,
        )
        return resp.choices[0].message.content or ""

    async def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        tool_map: dict[str, ToolDefinition],
    ) -> Any:
        """
        Invoke a tool handler by name. Handles both async and sync callables.
        Returns an error string on failure so the model can recover gracefully.
        """
        if name not in tool_map:
            msg = f"Unknown tool '{name}'. Available: {list(tool_map)}"
            log.error("%r: %s", self, msg)
            return f"ERROR: {msg}"

        handler = tool_map[name].handler
        try:
            if inspect.iscoroutinefunction(handler):
                return await handler(**args)
            # Run synchronous handlers in a thread to avoid blocking the event loop.
            return await asyncio.to_thread(lambda: handler(**args))
        except Exception as exc:
            log.error("%r: tool %r raised %s: %s", self, name, type(exc).__name__, exc)
            return f"ERROR calling '{name}': {exc}"

    # ------------------------------------------------------------------
    # Context helpers — available to all subclasses
    # ------------------------------------------------------------------

    def _format_context(self, context: dict[str, AgentResult]) -> str:
        """
        Format upstream dependency results as a readable Markdown context block.
        Returns an empty string if context is empty.
        """
        if not context:
            return ""
        parts = ["## Context from prior steps\n"]
        for result in context.values():
            status = "✓" if result.ok else "✗ ERROR"
            body   = result.output if result.ok else result.error
            parts.append(f"### [{result.role}] {status}\n{body}\n")
        return "\n".join(parts)

    def _build_messages(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
        *,
        system_prompt: str | None = None,
        extra: str = "",
    ) -> list[dict]:
        """
        Build a standard [system, user] message list.

        Composes upstream context, the task prompt, and any extra instructions
        into the user turn. The system turn uses `system_prompt` if given,
        otherwise falls back to `self.system_prompt`.

        Args:
            task:          The current AgentTask.
            context:       Upstream AgentResults.
            system_prompt: One-off system prompt override.
            extra:         Additional instructions appended after the task prompt.
        """
        ctx      = self._format_context(context)
        sys_text = system_prompt or self.system_prompt
        user_parts = [p for p in (ctx, f"Task: {task.prompt}", extra) if p]
        return [
            {"role": "system", "content": sys_text},
            {"role": "user",   "content": "\n\n".join(user_parts)},
        ]

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"role={self.role!r}, "
            f"model={self.spec})"
        )
