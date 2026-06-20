"""Agentic tool-calling loop for function-based runners.

ARCHITECTURE (routing + TYPE_CHECKING + Registry):
==================================================
This module sits at the boundary between supervisor/ (orchestration) and
tools/ (definitions) + agents/ (ToolDefinition type). It is the only
module under tools/ that legitimately needs supervisor/ types at
runtime (TaggedResult, infer_source, log_tool_call).

To avoid the cycle tools -> supervisor -> tools, the utils.logging
imports live inside the function body of ``_call_with_tools`` (lazy),
not at module level. Only leaf modules — config.settings, config.timeouts,
utils.llm_utils — are imported at the top.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import TYPE_CHECKING, Any, Final, Literal

from config.settings import ModelSpec
from config.timeouts import TOOL_TIMEOUT

from utils.llm_utils import _call_llm, _get_client

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition
    from memory.shared import MemoryManager
    from utils.logging.source_types import TaggedResult

log = logging.getLogger("goat2.tools.tool_runner")
__all__ = ["_call_with_tools", "_MAX_TOOL_CALLS_PER_TURN"]


# Hard cap on the total number of tool calls per turn.
# The previous behaviour let the LLM iterate up to 8 rounds × N
# tools per round, observed in the wild as 17 tool calls in a
# single turn (session 10:57, 2026-06-20 — the model burned
# 10 of those on `memory_get` for keys that didn't exist).
# The cap is loaded from config/goat.toml [supervisor]; default
# 6 is enough for normal flows and low enough to prevent the
# pathological "many calls, all no-ops" pattern.
def _load_tool_call_cap() -> int:
    """Read [supervisor].max_tool_calls_per_turn from config.

    Resolution order: toml > module default. The loader is
    best-effort so a missing file silently falls back.
    """
    default = 6
    try:
        from config.modular_loader import load_goat_config
        section = (load_goat_config() or {}).get("supervisor", {}) or {}
        raw = section.get("max_tool_calls_per_turn")
        if raw is not None:
            return max(1, int(raw))
    except Exception:  # noqa: BLE001 — best-effort
        pass
    return default


_MAX_TOOL_CALLS_PER_TURN: int = _load_tool_call_cap()

_MAX_ROUNDS: Final[int] = 8
ToolChoice = Literal["auto", "required", "none"]


_COERCIBLE = {"integer", "number", "boolean"}


def _coerce_arg(value: Any, prop_schema: dict) -> tuple[Any, str | None]:
    """Tolerantly coerce ``value`` to the JSON-Schema ``type`` in ``prop_schema``.

    LLMs sometimes emit string literals where a schema expects a number/boolean
    (e.g. ``{"limit": "50"}``). The OpenAI-compatible SDK used as the universal
    transport rejects those with HTTP 400 *before* dispatch, so we coerce
    up-front. Provider-agnostic — applies regardless of which model is
    configured. Returns an error string when conversion fails so the model
    can self-correct next round.
    """
    expected = prop_schema.get("type")
    if expected is None or expected not in _COERCIBLE or not isinstance(value, str):
        return value, None
    if expected in ("integer", "number"):
        try:
            return (int(value) if expected == "integer" else float(value)), None
        except (TypeError, ValueError):
            return value, f"parameter expected {expected}, got string {value!r}; pass a numeric literal"
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true", None
    return value, f"parameter expected boolean, got string {value!r}"


def _prepare_args(args: dict, tool_map: dict, name: str) -> tuple[dict, str | None]:
    """Apply defaults, coerce scalar types, and validate required parameters."""
    if name not in tool_map:
        return args, f"unknown tool '{name}'"
    schema = tool_map[name].parameters
    props = schema.get("properties", {}) or {}
    required = schema.get("required", [])

    for prop_name, prop_schema in props.items():
        if prop_name not in args and "default" in prop_schema:
            args[prop_name] = prop_schema["default"]
        if prop_name in args:
            coerced, type_err = _coerce_arg(args[prop_name], prop_schema)
            if type_err is not None:
                return args, f"ERROR: {name}.{prop_name}: {type_err}"
            args[prop_name] = coerced

    missing = [p for p in required if p not in args]
    if missing:
        return args, f"ERROR: missing required parameters for '{name}': {missing}"
    return args, None


def _serialize_arguments(raw: str) -> str:
    """Re-serialize tool arguments for consistent formatting in history."""
    try:
        parsed = json.loads(raw or "{}")
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    except json.JSONDecodeError:
        return raw  # fallback to original


def _strip_dsml(text: str) -> str:
    """Remove DeepSeek DSML markers from text to prevent LLM confusion.

    DeepSeek's model outputs tool calls in two formats:
    1. Structured (msg.tool_calls) - parsed correctly by GOAT
    2. Embedded in text content - DSML markers that confuse the LLM

    This strips DSML markers from text before adding to history.
    Supports both matching pairs (same content) and mixed pairs (invoke/tool_calls).
    """
    import re
    # Strip full wrapper tags with SAME content: <｜｜DSML｜｜...>...</｜｜DSML｜｜...>
    text = re.sub(r'<\｜｜DSML｜｜[^>]*>.*?</\｜｜DSML｜｜[^>]*>', '', text, flags=re.DOTALL)
    # Strip mixed pairs: <tag1>...</tag2> where content differs (invoke vs tool_calls)
    text = re.sub(r'<\｜｜DSML｜｜\w+>[^<]*</\｜｜DSML｜｜\w+>', '', text, flags=re.DOTALL)
    # Strip orphaned opening tags
    text = re.sub(r'<\｜｜DSML｜｜[^>]*>', '', text)
    return text.strip()


async def _dispatch(name: str, args: dict, tool_map: dict, memory_manager) -> str:
    """Dispatch a tool call by name, injecting memory_manager for memory tools if accepted.

    Note: Arguments are pre-validated by _prepare_args() before calling this function.
    """
    h = tool_map[name].handler
    # Inject memory_manager for memory tools only if handler accepts it
    if name.startswith("memory_") and "memory_manager" not in args:
        handler_params = inspect.signature(h).parameters
        if "memory_manager" in handler_params:
            args["memory_manager"] = memory_manager
    try:
        coro = h(**args) if inspect.iscoroutinefunction(h) else asyncio.to_thread(lambda: h(**args))
        out = await asyncio.wait_for(coro, timeout=TOOL_TIMEOUT)
        return str(out)
    except asyncio.TimeoutError:
        log.warning("tool '%s' timed out after %ds", name, TOOL_TIMEOUT)
        return f"ERROR: tool '{name}' timed out after {TOOL_TIMEOUT}s"
    except Exception as exc:
        return f"ERROR calling '{name}': {exc}"


async def _call_with_tools(
    spec: ModelSpec,
    messages: list[dict],
    tools: list[ToolDefinition],
    *,
    temperature: float = 0.2,
    tool_choice: ToolChoice = "auto",
    memory_manager: "MemoryManager | None" = None,
) -> "TaggedResult":
    """Tool-calling LLM loop; falls back to _call_llm when tools is empty or unsupported.

    Arguments are validated and defaults applied via _prepare_args() before dispatch.

    Args:
        spec: Model specification
        messages: Conversation messages
        tools: Available tools
        temperature: LLM temperature
        tool_choice: "auto", "required", or "none"
        memory_manager: Optional MemoryManager for injection into memory tools

    Returns:
        TaggedResult with content, inferred source tag, and list of called tool names.
    """
    # Lazy imports — break tools -> supervisor -> tools cycle.
    # These run only when _call_with_tools is actually called, not at import time.
    from utils.logging.source_types import (
        TaggedResult,
        TOOL_SOURCE_MAP,
        infer_source,
    )
    from utils.logging.structured_logger import log_tool_call

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
            return TaggedResult(content=_strip_dsml(final_content), source=source,
                                called_tools=tuple(called_tools))
        log.debug("tool_runner: round=%d calls=%s", rnd, [tc.function.name for tc in msg.tool_calls])
        # Strip DSML markers from content to prevent LLM confusion in subsequent rounds
        clean_content = _strip_dsml(msg.content or "")
        history.append({
            "role": "assistant", "content": clean_content,
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
            # BUG-?: per-turn tool-call cap. When the model
            # exceeds _MAX_TOOL_CALLS_PER_TURN, stop dispatching
            # and return a short honest fallback instead of
            # letting the loop keep spinning (or returning
            # empty). The fallback mentions the cap so the
            # operator can see why iteration stopped.
            if len(called_tools) + 1 > _MAX_TOOL_CALLS_PER_TURN:
                log.warning(
                    "tool_runner: per-turn cap reached "
                    "(%d calls) — returning fallback",
                    _MAX_TOOL_CALLS_PER_TURN,
                )
                source = infer_source(called_tools)
                fallback = (
                    f"[Reached the {_MAX_TOOL_CALLS_PER_TURN}-tool "
                    f"per-turn limit while answering. Stopped "
                    f"here.]"
                )
                return TaggedResult(
                    content=fallback,
                    source=source,
                    called_tools=tuple(called_tools),
                )
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                log.warning("tool_runner: invalid JSON arguments for '%s': %.120r",
                            tc.function.name, tc.function.arguments)
                args = {}
            args, error = _prepare_args(args, tool_map, tc.function.name)
            if error:
                result = error
            else:
                result = await _dispatch(tc.function.name, args, tool_map, memory_manager)
            # Strip DSML from tool results to keep history clean
            clean_result = _strip_dsml(result)
            called_tools.append(tc.function.name)
            tool_src = TOOL_SOURCE_MAP.get(tc.function.name, "generated")
            log_tool_call(tc.function.name, args, tool_src, clean_result)
            history.append({"role": "tool", "tool_call_id": tc.id, "content": clean_result})
            log.debug("tool %s → %.80s", tc.function.name, clean_result)
    resp = await client.chat.completions.create(model=spec.model_id, messages=history)
    source = infer_source(called_tools)
    # Also strip DSML from final response
    final_content = _strip_dsml(resp.choices[0].message.content or "")
    return TaggedResult(content=final_content, source=source,
                        called_tools=tuple(called_tools))
