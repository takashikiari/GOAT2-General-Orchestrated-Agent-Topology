"""goat_skills.fetch_content — fetch clean markdown from a URL via Crawl4AI.

GOAT calls this when it needs to read the content of a web page — articles,
documentation, any text-heavy page. Returns LLM-ready markdown.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools.web_config import WEB_MAX_CHARS, WEB_TIMEOUT
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]

_DESCRIPTION = (
    "Fetch and extract clean markdown content from a URL. Use this to read "
    "articles, documentation, or any text-heavy web page. Returns LLM-ready "
    "markdown, truncated to max_chars."
)


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Build the fetch_content tool."""

    async def handler(url: str, max_chars: int = WEB_MAX_CHARS, chat_id: str = "") -> str:
        try:
            from crawl4ai import AsyncWebCrawler
        except ImportError:
            return "(crawl4ai not installed — run: pip install crawl4ai)"

        class _Silent:
            def debug(self, *a, **kw): pass
            def info(self, *a, **kw): pass
            def success(self, *a, **kw): pass
            def warning(self, *a, **kw): pass
            def error(self, *a, **kw): pass
            def url_status(self, *a, **kw): pass
            def error_status(self, *a, **kw): pass

        log.info("fetch_content url=%s max_chars=%d", url, max_chars)
        try:
            async with AsyncWebCrawler(logger=_Silent()) as crawler:
                result = await crawler.arun(
                    url=url,
                    page_timeout=WEB_TIMEOUT * 1000,
                )
            if not result.success:
                return f"(failed to fetch {url}: {result.error_message})"
            content = result.markdown or result.cleaned_html or ""
            if not content:
                return f"(no content extracted from {url})"
            truncated = content[:max_chars]
            log.debug("fetch_content url=%s chars=%d", url, len(truncated))
            return truncated
        except Exception as exc:
            log.warning("fetch_content error url=%s: %s", url, exc)
            return f"(error fetching {url}: {exc})"

    return [ToolDefinition(
        name="fetch_content",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_chars": {"type": "integer", "description": f"Max chars to return (default {WEB_MAX_CHARS})"},
            },
            "required": ["url"],
        },
        handler=handler,
    )]
