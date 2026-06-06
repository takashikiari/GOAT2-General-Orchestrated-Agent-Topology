"""Agentic tool-calling loop for function-based runners."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import TYPE_CHECKING, Any, Final, Literal

from config.settings import ModelSpec

from supervisor.llm_utils import _call_llm, _get_client
from supervisor.source_types import TaggedResult, TOOL_SOURCE_MAP, infer_source
from supervisor.structured_logger import log_tool_call

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tool_runner")
__all__ = ["_call_with_tools"]

_MAX_ROUNDS: Final[int] = 8
ToolChoice = Literal["auto", "required", "none"]


def _apply_defaults(args: dict, tool_map: dict, name: str) -> dict:
    """Complete missing arguments with default values from the tool's parameter schema."""
    if name not in tool_map:
        return args
    schema = tool_map[name].parameters
    for prop_name, prop_schema in schema.get("properties", {}).items():
        if prop_name not in args and "default" in prop_schema:
            args[prop_name] = prop_schema["default"]
    return args


def _serialize_arguments(raw: str) -> str:
    """Re-serialize tool arguments for consistent formatting in history."""
    try:
        parsed = json.loads(raw or "{}")
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    except json.JSONDecodeError:
        return raw  # fallback to original


async def _dispatch(name: str, args: dict, tool_map: dict) -> str:
    if name not in tool_map:
        return f"ERROR: unknown tool '{name}'"
    h = tool_map[name].handler
    try:
        out = await h(**args) if inspect.iscoroutinefunction(h) else await asyncio.to_thread(lambda: h(**args))
        return str(out)
    except Exception as exc:
        return f"ERROR calling '{name}': {exc}"


async def _call_with_tools(
    spec: ModelSpec,
    messages: list[dict],
    tools: list[ToolDefinition],
    *,
    temperature: float = 0.2,
    tool_choice: ToolChoice = "auto",
) -> TaggedResult:
    """Tool-calling LLM loop; falls back to _call_llm when tools is empty or unsupported.

    Returns a TaggedResult with content, the inferred source tag, and the list
    of tool names that were actually called during this invocation.
    """
    if not tools or not spec.tool_calling:
        log.debug("tool_runner bypass: model=%s tools=%d tool_calling=%s",
                  spec.model_id, len(tools), spec.tool_calling)
        content = await _call_llm(spec, messages, temperature=temperature)
        return TaggedResult(content=content, source="generated")
    client = _get_client(spec)
    tool_map = {t.name: t for t in tools}
    schema = [t.to_openai() for t in tools]
    history = list(messages)
    called_tools: list[str] = []
    log.debug("tool_runner: model=%s tools=%s choice=%s", spec.model_id, [t.name for t in tools], tool_choice)
    for rnd in range(_MAX_ROUNDS + 1):
        kw: dict[str, Any] = {"model": spec.model_id, "messages": history}
        if not spec.no_temperature:
            kw["temperature"] = temperature
        if rnd < _MAX_ROUNDS:
            kw |= {"tools": schema, "tool_choice": tool_choice}
        resp = await client.chat.completions.create(**kw)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            log.debug("tool_runner: round=%d no tool_calls from %s", rnd, spec.model_id)
            source = infer_source(called_tools)
            final_content = msg.content or ""
            if not final_content.strip() and history:
                last_tool = next(
                    (m["content"] for m in reversed(history) if m.get("role") == "tool"),
                    ""
                )
                final_content = last_tool
            return TaggedResult(content=final_content, source=source,
                                called_tools=tuple(called_tools))
        log.debug("tool_runner: round=%d calls=%s", rnd, [tc.function.name for tc in msg.tool_calls])
        history.append({
            "role": "assistant", "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": _serialize_arguments(tc.function.arguments),
                    },
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                log.warning("tool_runner: invalid JSON arguments for '%s': %.120r",
                            tc.function.name, tc.function.arguments)
                args = {}
            args = _apply_defaults(args, tool_map, tc.function.name)
            result = await _dispatch(tc.function.name, args, tool_map)
            called_tools.append(tc.function.name)
            tool_src = TOOL_SOURCE_MAP.get(tc.function.name, "generated")
            log_tool_call(tc.function.name, args, tool_src, result)
            history.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            log.debug("tool %s → %.80s", tc.function.name, result)
    resp = await client.chat.completions.create(model=spec.model_id, messages=history)
    source = infer_source(called_tools)
    return TaggedResult(content=resp.choices[0].message.content or "", source=source,
                        called_tools=tuple(called_tools))
