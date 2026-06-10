"""Web search tool — queries local SearXNG instance.

Provides a single ToolDefinition (WEB_SEARCH) that sends a search query to
a local SearXNG instance and returns formatted text snippets.

Configuration (via environment variables):
- SEARXNG_URL: Full URL of SearXNG instance (default: http://localhost:7777)
- SEARCH_TIMEOUT: Request timeout in seconds (default: 10.0)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Final

import httpx

from agents.base_agent import ToolDefinition

__all__ = ["WEB_SEARCH"]

log = logging.getLogger("goat2.web_search")

_DEFAULT_URL: Final[str] = "http://localhost:7777"
_TIMEOUT: Final[float] = 10.0
_DEFAULT_N: Final[int] = 5

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
    return "\n".join(lines) if lines else f"No results found for: {query!r}"


WEB_SEARCH = ToolDefinition(
    name="web_search",
    description=(
        "Search the web via local SearXNG instance and return text snippets. "
        f"Configure via SEARXNG_URL env var (default: {_DEFAULT_URL})."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)