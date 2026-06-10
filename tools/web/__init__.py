"""Web search tool — queries local SearXNG instance.

Provides WEB_SEARCH ToolDefinition that sends search queries to
a local SearXNG instance and returns formatted text snippets.

CONFIGURATION:
==============
- SEARXNG_URL: Full URL of SearXNG instance (default: http://localhost:7777)
- SEARCH_TIMEOUT: Request timeout in seconds (default: 10.0)

TOOL EXPORTS:
============
- WEB_SEARCH: Search the web via local SearXNG instance
"""

from __future__ import annotations

from tools.web.web_search import WEB_SEARCH

__all__ = ["WEB_SEARCH"]