"""Web search tool — queries local SearXNG instance.

Provides a single ToolDefinition (WEB_SEARCH) that sends a search query to
a local SearXNG instance and returns formatted text snippets.

Configuration resolution (highest priority first):
  1. SEARXNG_URL env var        → overrides ``[web_search].url``
  2. SEARCH_TIMEOUT env var     → overrides ``[web_search].timeout_seconds``
  3. config/tools.toml          → ``[web_search]`` section
  4. module-level fallback      → http://localhost:7777 / 10.0s / 5 results
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Final

import httpx

from tools._make_tool import make_tool

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.web.search")

__all__ = ["WEB_SEARCH"]

# Defaults — overridden by config/tools.toml [web_search] at import time.
_DEFAULTS: Final[dict[str, object]] = {
    "url":             "http://localhost:7777",
    "timeout_seconds": 10.0,
    "default_results": 5,
}


def _load_web_search_config() -> dict[str, object]:
    """Read [web_search] from config/tools.toml with env-var override.

    The toml loader is non-fatal — a missing or unparseable file
    silently falls back to the module defaults, so the tool stays
    usable in any environment.

    Returns:
        dict with keys ``url`` (str), ``timeout_seconds`` (float),
        ``default_results`` (int). Values are post-resolution.
    """
    cfg: dict[str, object] = dict(_DEFAULTS)
    try:
        from config.modular_loader import load_tools_config
        toml_cfg = load_tools_config()
        section = toml_cfg.get("web_search", {}) or {}
        for key in ("url", "timeout_seconds", "default_results"):
            if key in section and section[key] is not None:
                cfg[key] = section[key]
    except Exception as exc:
        log.debug("web_search: tools.toml [web_search] load skipped: %s", exc)
    # Env-var overrides.
    if os.environ.get("SEARXNG_URL"):
        cfg["url"] = os.environ["SEARXNG_URL"]
    if os.environ.get("SEARCH_TIMEOUT"):
        try:
            cfg["timeout_seconds"] = float(os.environ["SEARCH_TIMEOUT"])
        except ValueError:
            log.warning(
                "web_search: SEARCH_TIMEOUT=%r not a float — using default",
                os.environ["SEARCH_TIMEOUT"],
            )
    return cfg


_WEB_SEARCH_CONFIG: Final[dict[str, object]] = _load_web_search_config()
_DEFAULT_URL: Final[str] = str(_WEB_SEARCH_CONFIG["url"])
_TIMEOUT: Final[float] = float(_WEB_SEARCH_CONFIG["timeout_seconds"])
_DEFAULT_N: Final[int] = int(_WEB_SEARCH_CONFIG["default_results"])

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query.",
        },
        "num_results": {
            "type": "integer",
            "description": f"Max snippets to return (default {_DEFAULT_N}).",
            "default": _DEFAULT_N,
        },
    },
    "required": ["query"],
}


def _parse_searxng(data: dict, num_results: int) -> list[str]:
    """Extract text snippets from SearXNG JSON response."""
    lines: list[str] = []
    for result in data.get("results", []):
        title = result.get("title", "").strip()
        url = result.get("url", "").strip()
        content = result.get("content", "").strip()

        if title:
            lines.append(f"[{title}]({url})")
        if content:
            lines.append(f"- {content}")

        if len(lines) >= num_results * 2:  # *2 for title+content pairs
            break
    return lines


async def _handler(query: str, num_results: int = _DEFAULT_N) -> str:
    """Query local SearXNG instance; return formatted snippets."""
    log.debug("web_search: query=%r num_results=%d", query, num_results)
    base_url = os.environ.get("SEARXNG_URL", _DEFAULT_URL).rstrip("/")
    url = f"{base_url}/search"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                url,
                params={
                    "q": query,
                    "format": "json",
                    "lang": "en",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        log.warning("web_search: timeout for query=%.80r", query)
        return "ERROR: search request timed out"
    except httpx.HTTPStatusError as exc:
        log.warning("web_search: HTTP %d for query=%.80r", exc.response.status_code, query)
        return f"ERROR: search API returned HTTP {exc.response.status_code}"
    except json.JSONDecodeError:
        log.warning("web_search: invalid JSON response for query=%.80r", query)
        return "ERROR: search API returned invalid JSON"
    except Exception as exc:
        log.exception("web_search: unexpected error for query=%.80r", query)
        return f"ERROR: search request failed: {exc}"

    lines = _parse_searxng(data, num_results)
    log.debug("web_search: results=%d (capped at %d)", len(lines) // 2, num_results)
    return "\n".join(lines) if lines else f"No results found for: {query!r}"


WEB_SEARCH = make_tool(
    name="web_search",
    description=(
        "Search the web via local SearXNG instance and return text snippets. "
        f"Configure via SEARXNG_URL env var or [web_search].url in tools.toml (default: {_DEFAULT_URL})."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)