from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from config.settings import Provider, PROVIDER_BASE_URLS, ModelSpec, settings
from supervisor.types import AgentResult

__all__ = [
    "_get_client", "_call_llm", "_extract_json",
    "_format_dep_context", "_model_label",
]

log = logging.getLogger("goat2.llm_utils")

_clients: dict[str, AsyncOpenAI] = {}


def _get_client(spec: ModelSpec) -> AsyncOpenAI:
    """Return a cached AsyncOpenAI client for the spec's provider."""
    key = spec.provider.value
    if key not in _clients:
        _clients[key] = AsyncOpenAI(
            api_key=settings.api_keys.for_provider(spec.provider),
            base_url=PROVIDER_BASE_URLS[key],
        )
    return _clients[key]


async def _call_llm(
    spec: ModelSpec,
    messages: list[dict],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
) -> str:
    """Send messages to the LLM and return the text content of the first choice."""
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
    """Format upstream AgentResults as a Markdown context block for the next agent."""
    if not dep_results:
        return ""
    parts = ["## Context from prior steps\n"]
    for r in dep_results.values():
        status = "✓" if r.ok else "✗ ERROR"
        # Truncate long outputs to avoid overflowing the context window (P8 fix)
        output = r.output if r.ok else r.error
        if output and len(output) > 4000:
            output = output[:4000] + "\n\n[... truncated ...]"
        parts.append(f"### [{r.role}] {status}\n{output}\n")
    return "\n".join(parts)


def _model_label(role: str) -> str:
    try:
        return str(settings.agents.get(role))
    except (ValueError, AttributeError):
        return f"{role}/custom"
