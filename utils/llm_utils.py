"""LLM client utilities — cached OpenAI clients, message formatting, JSON extraction.

MESSAGE SIZE MANAGEMENT:
=======================
All functions that construct messages for LLM calls enforce size limits to prevent
'Message is too long' errors from the API. The key limits are:

1. _format_dep_context: truncates each result at 4000 chars, AND caps total context at 32000 chars
2. _call_llm: validates total message size before sending, truncates if needed
3. Individual message content is capped at 64000 chars per message

These limits apply to all LLM calls across the supervisor module.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from config.settings import Provider, PROVIDER_BASE_URLS, ModelSpec, Settings

if TYPE_CHECKING:
    from supervisor.types import AgentResult

__all__ = [
    "_get_client", "_call_llm", "_extract_json",
    "_format_dep_context", "_model_label",
]

log = logging.getLogger("goat2.llm_utils")

_clients: dict[str, AsyncOpenAI] = {}

# ── Size limits to prevent "Message is too long" errors ──
_MAX_CONTENT_CHARS: int = 64000       # max chars per single message content
_MAX_CONTEXT_CHARS: int = 32000       # max chars for accumulated dep context
_MAX_RESULT_CHARS: int = 4000         # max chars per individual result in context
_MAX_TOTAL_MESSAGES_CHARS: int = 128000  # max total chars across all messages


def _get_client(spec: ModelSpec) -> AsyncOpenAI:
    """Return a cached AsyncOpenAI client for the spec's provider."""
    key = spec.provider.value
    if key not in _clients:
        # Import Settings on demand to avoid circular imports
        _clients[key] = AsyncOpenAI(
            api_key=Settings().api_keys.for_provider(spec.provider),
            base_url=PROVIDER_BASE_URLS[key],
        )
    return _clients[key]


def _truncate_content(content: str, max_chars: int = _MAX_CONTENT_CHARS) -> str:
    """Truncate content to max_chars, appending a truncation notice if needed.

    Args:
        content: The string to potentially truncate.
        max_chars: Maximum character count (default: _MAX_CONTENT_CHARS).

    Returns:
        Truncated string with a notice if truncation occurred.
    """
    if not content or len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    log.warning(
        "Content truncated from %d to %d chars",
        len(content), max_chars,
    )
    return truncated + "\n\n[... content truncated due to size limit ...]"


def _truncate_messages(messages: list[dict]) -> list[dict]:
    """Ensure all messages respect size limits; truncate individual and total content.

    Iterates through messages and truncates any 'content' field that exceeds
    _MAX_CONTENT_CHARS. Also checks total size across all messages and warns if
    it exceeds _MAX_TOTAL_MESSAGES_CHARS.

    Args:
        messages: List of message dicts with 'content' key.

    Returns:
        Messages with truncated content where necessary.
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and len(content) > _MAX_CONTENT_CHARS:
            msg["content"] = _truncate_content(content, _MAX_CONTENT_CHARS)
            content = msg["content"]
        total_chars += len(content) if isinstance(content, str) else 0

    if total_chars > _MAX_TOTAL_MESSAGES_CHARS:
        log.warning(
            "Total message size %d chars exceeds limit %d — may cause API errors",
            total_chars, _MAX_TOTAL_MESSAGES_CHARS,
        )
    return messages


async def _call_llm(
    spec: ModelSpec,
    messages: list[dict],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
) -> str:
    """Send messages to the LLM and return the text content of the first choice.

    Automatically truncates messages that exceed size limits to prevent
    'Message is too long' API errors.

    Args:
        spec: ModelSpec with model_id, provider, etc.
        messages: List of message dicts (system, user, assistant roles).
        json_mode: If True, request JSON response format (OpenAI only).
        temperature: Sampling temperature (default 0.2).

    Returns:
        The text content of the first choice from the LLM response.
    """
    # Truncate oversized messages before sending
    messages = _truncate_messages(messages)

    client = _get_client(spec)
    kwargs: dict[str, Any] = {"model": spec.model_id, "messages": messages}
    if not spec.no_temperature:
        kwargs["temperature"] = temperature
    if json_mode and spec.provider == Provider.OPENAI:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _extract_balanced_json(text: str) -> Any:
    """
    Extract a JSON object using brace-balance counting.
    More precise than greedy `{...}` regex — stops at the first balanced top-level object.
    """
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No opening brace found", text, 0)
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                return json.loads(candidate)
    raise json.JSONDecodeError("Unbalanced braces", text, start)


def _extract_json(text: str) -> Any:
    """Extract a JSON object/array from text that may contain markdown fences."""
    parsers = [
        ("direct", lambda t: json.loads(t)),
        ("fenced", lambda t: json.loads(re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", t).group(1))),  # type: ignore[union-attr]
        ("balanced", _extract_balanced_json),
        ("greedy_fallback", lambda t: json.loads(re.search(r"\{[\s\S]+\}", t).group(0))),  # type: ignore[union-attr]
    ]
    for label, parser in parsers:
        try:
            result = parser(text)
            log.debug("_extract_json: parser=%s succeeded", label)
            return result
        except (json.JSONDecodeError, AttributeError, IndexError):
            continue
    raise ValueError(f"Could not extract JSON from output:\n{text[:400]}")


def _format_dep_context(dep_results: dict[str, AgentResult]) -> str:
    """Format upstream AgentResults as a Markdown context block for the next agent.

    Enforces size limits to prevent 'Message is too long' errors:
    - Each individual result is truncated at _MAX_RESULT_CHARS (4000) chars
    - Total context is capped at _MAX_CONTEXT_CHARS (32000) chars
    - If total exceeds limit, later results are dropped with a notice

    Args:
        dep_results: Dictionary of task_id → AgentResult from upstream tasks.

    Returns:
        Formatted Markdown string, possibly truncated.
    """
    if not dep_results:
        return ""
    parts = ["## Context from prior steps\n"]
    total_len = 0
    for r in dep_results.values():
        status = "✓" if r.ok else "✗ ERROR"
        # Truncate long outputs to avoid overflowing the context window
        output = r.output if r.ok else r.error
        if output and len(output) > _MAX_RESULT_CHARS:
            output = output[:_MAX_RESULT_CHARS] + "\n\n[... truncated ...]"
        entry = f"### [{r.role}] {status}\n{output}\n"
        # Check if adding this entry would exceed the total context limit
        if total_len + len(entry) > _MAX_CONTEXT_CHARS:
            remaining = len(dep_results) - len(parts) + 1
            if remaining > 0:
                parts.append(
                    f"\n[... {remaining} more result(s) omitted due to context size limit ...]"
                )
            break
        parts.append(entry)
        total_len += len(entry)
    return "\n".join(parts)


def _model_label(role: str) -> str:
    try:
        return str(Settings().agents.get(role))
    except (ValueError, AttributeError):
        return f"{role}/custom"
