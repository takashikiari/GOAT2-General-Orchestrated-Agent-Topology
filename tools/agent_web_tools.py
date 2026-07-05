"""tools.agent_web_tools — web tools for DAG agents using crawl4ai.

Uses the same AsyncWebCrawler backend as GOAT's fetch_content goat_skill,
so search and fetch quality is identical between orchestrator and DAG agents.

WEB_SEARCH  — query a search engine, return markdown results
FETCH_URL   — fetch a specific URL and return LLM-ready markdown
"""
from __future__ import annotations

import logging

from tools.types import AgentTool
from tools.web_config import WEB_MAX_CHARS, WEB_TIMEOUT

log = logging.getLogger("goat2.tools.agent_web")

_SEARCH_URL = "https://lite.duckduckgo.com/lite/"
_SEARCH_MAX = 6_000


async def _crawl(url: str, max_chars: int) -> str:
    """Shared crawl4ai fetch — mirrors fetch_content.py exactly."""
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError:
        return "(crawl4ai not installed — run: pip install crawl4ai)"
    try:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url, page_timeout=WEB_TIMEOUT * 1000)
        if not result.success:
            return f"(failed to fetch {url}: {result.error_message})"
        content = result.markdown or result.cleaned_html or ""
        if not content:
            return f"(no content extracted from {url})"
        if len(content) > max_chars:
            omitted = len(content) - max_chars
            return content[:max_chars] + f"\n...[{omitted} chars omitted]"
        return content
    except Exception as exc:
        log.warning("crawl error url=%s: %s", url, exc)
        return f"(error fetching {url}: {exc})"


# ── WEB_SEARCH ────────────────────────────────────────────────────────────────

async def _web_search(query: str, max_chars: int = _SEARCH_MAX) -> str:
    import urllib.parse
    mc = max(500, min(int(max_chars), 20_000))
    search_url = f"{_SEARCH_URL}?q={urllib.parse.quote_plus(query)}&kl=us-en"
    log.info("web_search query=%r url=%s", query, search_url)
    return await _crawl(search_url, mc)

WEB_SEARCH = AgentTool(
    name="web_search",
    description=(
        "Search the web using DuckDuckGo and return LLM-ready markdown results. "
        "Use for finding current information, documentation, or facts not in memory. "
        "Same crawl4ai backend as GOAT's fetch_content skill."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query":     {"type": "string", "description": "Search query"},
            "max_chars": {"type": "integer",
                          "description": f"Max chars to return (default {_SEARCH_MAX})"},
        },
        "required": ["query"],
    },
    handler=_web_search,
)


# ── FETCH_URL ─────────────────────────────────────────────────────────────────

async def _fetch_url(url: str, max_chars: int = WEB_MAX_CHARS) -> str:
    mc = max(500, min(int(max_chars), 100_000))
    log.info("fetch_url url=%s max_chars=%d", url, mc)
    return await _crawl(url, mc)

FETCH_URL = AgentTool(
    name="fetch_url",
    description=(
        "Fetch a specific URL and return its content as LLM-ready markdown. "
        "Same crawl4ai backend as GOAT's fetch_content skill. "
        "Use after web_search when you need the full content of a specific result."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url":       {"type": "string", "description": "URL to fetch"},
            "max_chars": {"type": "integer",
                          "description": f"Max chars to return (default {WEB_MAX_CHARS})"},
        },
        "required": ["url"],
    },
    handler=_fetch_url,
)
