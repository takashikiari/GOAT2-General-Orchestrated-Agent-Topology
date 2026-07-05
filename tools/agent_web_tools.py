"""tools.agent_web_tools — web search tool for DAG agents.

WEB_SEARCH fetches results from DuckDuckGo Lite (no API key required)
and returns a text summary.  Optionally falls back to crawl4ai for
richer extraction on individual URLs.
"""
from __future__ import annotations

import logging

from tools.types import AgentTool

log = logging.getLogger("goat2.tools.agent_web")

_DDG_URL = "https://lite.duckduckgo.com/lite/"
_MAX_CHARS = 6_000
_TIMEOUT = 20


async def _web_search(query: str, max_chars: int = _MAX_CHARS) -> str:
    try:
        import httpx
    except ImportError:
        return "ERROR: httpx not installed"

    mc = max(500, min(int(max_chars), 20_000))
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.post(
                _DDG_URL,
                data={"q": query, "kl": "us-en"},
                headers={"User-Agent": "Mozilla/5.0 (compatible; GOAT-agent/2.0)"},
            )
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        log.warning("web_search: fetch failed query=%r: %s", query, exc)
        return f"ERROR: web search failed: {exc}"

    # Extract text lines from DDG Lite HTML (simple, no heavy parser needed)
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s{2,}", "\n", text).strip()
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 20]
    result = "\n".join(lines)
    if len(result) > mc:
        result = result[:mc] + f"\n...[{len(result) - mc} chars truncated]"

    log.info("web_search: query=%r chars=%d", query, len(result))
    return result or f"No results found for: {query}"


WEB_SEARCH = AgentTool(
    name="web_search",
    description=(
        "Search the web using DuckDuckGo and return text results. "
        "Use for finding current information, documentation, or facts not in memory."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query":     {"type": "string", "description": "Search query"},
            "max_chars": {"type": "integer", "description": f"Max chars to return (default {_MAX_CHARS})"},
        },
        "required": ["query"],
    },
    handler=_web_search,
)
