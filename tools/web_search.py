"""Web search tool — queries DuckDuckGo instant answers (or custom backend).

Provides a single ToolDefinition (WEB_SEARCH) that sends a search query to
DuckDuckGo's instant-answer API and returns formatted text snippets. The
endpoint can be overridden via the SEARCH_API_URL environment variable.
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

_DDG_URL: Final[str] = "https://api.duckduckgo.com/"
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


def _parse_ddg(data: dict, num_results: int) -> list[str]:
    """Extract text snippets from a DuckDuckGo instant-answer JSON response."""
    lines: list[str] = []
    abstract = (data.get("AbstractText") or "").strip()
    if abstract:
        src = data.get("AbstractSource", "")
        lines.append(f"[{src}] {abstract}" if src else abstract)
    for topic in (data.get("RelatedTopics") or []):
        if not isinstance(topic, dict):
            continue
        text = (topic.get("Text") or "").strip()
        if text:
            lines.append(f"- {text}")
        if len(lines) >= num_results:
            break
    return lines


async def _handler(query: str, num_results: int = _DEFAULT_N) -> str:
    """
    Query DuckDuckGo instant answers; return formatted snippets.
    Override endpoint via SEARCH_API_URL env var (SerpAPI, Tavily, Brave, etc.).
    """
    url = os.environ.get("SEARCH_API_URL", _DDG_URL)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                url,
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
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

    lines = _parse_ddg(data, num_results)
    return "\n".join(lines) if lines else f"No results found for: {query!r}"


WEB_SEARCH = ToolDefinition(
    name="web_search",
    description=(
        "Search the web and return text snippets. "
        "Uses DuckDuckGo by default; set SEARCH_API_URL to use a different backend."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
