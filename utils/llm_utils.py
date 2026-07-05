"""utils.llm_utils — shared LLM client cache and call helpers.

Provides a cached AsyncOpenAI client per provider so all agent instances
share connections instead of creating a new TLS socket per call.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from config.agent_types import AgentResult
    from config.settings import ModelSpec

log = logging.getLogger("goat2.utils.llm")

# Cache keyed by "{provider}:{base_url}" so different providers never share a client.
_client_cache: dict[str, AsyncOpenAI] = {}


def _get_client(spec: "ModelSpec") -> AsyncOpenAI:
    """Return a cached AsyncOpenAI-compatible client for the spec's provider.

    Reads ``GOAT_{PROVIDER}_API_KEY`` from the environment.
    Example: provider=groq → GOAT_GROQ_API_KEY, provider=deepseek → GOAT_DEEPSEEK_API_KEY.
    """
    import os
    from config.settings import PROVIDER_BASE_URLS, TIMEOUT_SECONDS

    base_url = PROVIDER_BASE_URLS.get(spec.provider, "https://api.deepseek.com")
    cache_key = f"{spec.provider.value}:{base_url}"

    if cache_key not in _client_cache:
        provider_key_env = f"GOAT_{spec.provider.value.upper()}_API_KEY"
        api_key = os.environ.get(provider_key_env, "")
        _client_cache[cache_key] = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(TIMEOUT_SECONDS),
        )
        log.debug("llm_utils: created client provider=%s base_url=%s", spec.provider.value, base_url)

    return _client_cache[cache_key]


async def _call_llm(
    spec: "ModelSpec",
    messages: list[dict],
    *,
    temperature: float = 0.4,
    json_mode: bool = False,
) -> str:
    """Single-turn LLM call — no tool loop.

    Args:
        spec:        Model + provider spec.
        messages:    OpenAI-format message list.
        temperature: Sampling temperature.
        json_mode:   Request JSON output (only applied on Provider.OPENAI).

    Returns:
        Model's text content, or empty string.
    """
    from config.settings import Provider

    client = _get_client(spec)
    kwargs: dict[str, Any] = {
        "model":       spec.model_id,
        "messages":    messages,
        "temperature": temperature,
    }
    if json_mode and spec.provider == Provider.OPENAI:
        kwargs["response_format"] = {"type": "json_object"}

    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _format_dep_context(context: dict[str, "AgentResult"]) -> str:
    """Format upstream AgentResult objects as readable text for an LLM prompt."""
    if not context:
        return ""
    parts: list[str] = []
    for task_id, result in context.items():
        status = "✓" if result.ok else "✗ ERROR"
        body = result.output if result.ok else result.error
        parts.append(f"[{result.role} / {task_id}] {status}\n{body}")
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict:
    """Extract a JSON object from raw LLM output.

    Handles: bare JSON, markdown fences (```json ... ```), and JSON embedded
    in prose. Raises ``ValueError`` if no valid JSON is found.
    """
    stripped = text.strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    brace = re.search(r"\{.*\}", stripped, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in: {text[:200]!r}")
