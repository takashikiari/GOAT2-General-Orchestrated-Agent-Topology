"""Tool registry — exports all tool definitions and convenience groupings.

This module aggregates every ToolDefinition from the tools package into
well-known lists (ALL_TOOLS, FILE_TOOLS, MEMORY_TOOLS) and re-exports
individual constants for direct import.

DIRECTORY STRUCTURE:
==================
tools/
├── __init__.py          — this file, re-exports all tools
├── file/                — file operation tools
│   ├── __init__.py
│   ├── file_executor.py  — central security gateway
│   ├── file_create.py
│   ├── file_executor_helpers.py
│   ├── file_grep.py
│   ├── file_info.py
│   ├── file_list.py
│   ├── file_read.py
│   ├── file_read_lines.py
│   ├── file_search.py
│   ├── file_write.py
│   ├── file_storage_helpers.py
│   ├── file_storage_service.py
│   └── path_utils.py
├── memory/              — memory operation tools (shim, re-exports from memory.memory_tools)
├── web/                — web search tools
│   ├── __init__.py
│   └── web_search.py
├── system/             — system tools
│   ├── __init__.py
│   ├── calculator.py
│   ├── think.py
│   └── shell_tool.py
├── registry_accessor.py — global registry accessor
└── README.md           — module documentation
"""

from __future__ import annotations

# Re-export from file/ subdirectory
from tools.file import (
    FILE_CREATE,
    FILE_GREP,
    FILE_INFO,
    FILE_LIST,
    FILE_READ,
    FILE_READ_LINES,
    FILE_SEARCH,
    FILE_WRITE,
    EXECUTOR,
    FileToolExecutor,
    MAX_LIST,
    MAX_READ,
    MAX_WRITE,
    SUPPORTED_TEXT_EXTENSIONS,
)

# Re-export from memory/ subdirectory (shim that re-exports from memory.memory_tools)
from memory.memory_tools import (
    MEMORY_AUTO_PROMOTE,
    MEMORY_COUNT,
    MEMORY_DELETE,
    MEMORY_DIRECT_QUERY,
    MEMORY_EMBEDDING,
    MEMORY_EXPORT,
    MEMORY_GET,
    MEMORY_GET_DAG,
    MEMORY_LAST_WRITE,
    MEMORY_PROMOTE,
    MEMORY_RECENT,
    MEMORY_RECENT_DAG,
    MEMORY_SEARCH,
    MEMORY_SEARCH_DAG,
    MEMORY_STORE,
    MEMORY_STORE_DAG,
    MEMORY_TIMELINE,
    MEMORY_DEBUG_TRACE,
    MEMORY_TTL,
    MEMORY_UPDATE,
)

# Re-export from web/ subdirectory
from tools.web import WEB_SEARCH

# Re-export from system/ subdirectory
from tools.system import CALCULATOR, SHELL, THINK

from agents.base_agent import ToolDefinition
from tools.tool_runner import _call_with_tools

__all__ = [
    # Individual tools
    "THINK",
    "CALCULATOR",
    "WEB_SEARCH",
    "SHELL",
    "FILE_READ",
    "FILE_WRITE",
    "FILE_CREATE",
    "FILE_LIST",
    "FILE_SEARCH",
    "FILE_GREP",
    "FILE_INFO",
    "FILE_READ_LINES",
    "MEMORY_SEARCH",
    "MEMORY_GET",
    "MEMORY_STORE",
    "MEMORY_DELETE",
    "MEMORY_UPDATE",
    "MEMORY_TIMELINE",
    "MEMORY_RECENT",
    "MEMORY_DEBUG_TRACE",
    "MEMORY_DIRECT_QUERY",
    "MEMORY_LAST_WRITE",
    "MEMORY_COUNT",
    "MEMORY_TTL",
    "MEMORY_EXPORT",
    "MEMORY_PROMOTE",
    "MEMORY_AUTO_PROMOTE",
    "MEMORY_EMBEDDING",
    "_call_with_tools",
    # Convenience groups
    "DAG_MEMORY_TOOLS",
    "ALL_TOOLS",
    "FILE_TOOLS",
    "MEMORY_TOOLS",
    # Tool namespaces
    "DAG_NAMESPACE",
    "GOAT_NAMESPACE",
    "VALIDATOR_NAMESPACE",
    "PROMOTER_NAMESPACE",
]

# All tool definitions (17 total)
ALL_TOOLS: list[ToolDefinition] = [
    THINK,
    CALCULATOR,
    WEB_SEARCH,
    SHELL,
    FILE_READ,
    FILE_WRITE,
    FILE_CREATE,
    FILE_LIST,
    FILE_SEARCH,
    FILE_GREP,
    FILE_INFO,
    FILE_READ_LINES,
    MEMORY_SEARCH,
    MEMORY_GET,
    MEMORY_STORE,
    MEMORY_DELETE,
    MEMORY_UPDATE,
    MEMORY_TIMELINE,
    MEMORY_RECENT,
    MEMORY_DEBUG_TRACE,
    MEMORY_DIRECT_QUERY,
    MEMORY_LAST_WRITE,
    MEMORY_COUNT,
    MEMORY_TTL,
    MEMORY_EMBEDDING,
    MEMORY_EXPORT,
]

# File operation tools + web search
FILE_TOOLS: list[ToolDefinition] = [
    FILE_READ,
    FILE_WRITE,
    FILE_CREATE,
    FILE_LIST,
    FILE_SEARCH,
    FILE_GREP,
    FILE_INFO,
    FILE_READ_LINES,
    WEB_SEARCH,
    SHELL,
]

# Memory tools for GOAT supervisor (full tier access)
MEMORY_TOOLS: list[ToolDefinition] = [
    MEMORY_SEARCH,
    MEMORY_GET,
    MEMORY_STORE,
    MEMORY_DELETE,
    MEMORY_UPDATE,
    MEMORY_TIMELINE,
    MEMORY_RECENT,
    MEMORY_DEBUG_TRACE,
    MEMORY_DIRECT_QUERY,
    MEMORY_LAST_WRITE,
    MEMORY_COUNT,
    MEMORY_TTL,
    MEMORY_EMBEDDING,
    MEMORY_EXPORT,
    MEMORY_PROMOTE,
    MEMORY_AUTO_PROMOTE,
]

# Memory tools for DAG agents (working tier only)
DAG_MEMORY_TOOLS: list[ToolDefinition] = [
    MEMORY_SEARCH_DAG,
    MEMORY_GET_DAG,
    MEMORY_STORE_DAG,
    MEMORY_RECENT_DAG,
]

# ── TOOL NAMESPACE CONSTANTS ──
# Redis key namespaces for tool distribution
DAG_NAMESPACE: str = "dag"  # DAG agents: dag:* namespace
GOAT_NAMESPACE: str = "goat"  # GOAT conversational: goat:* namespace
VALIDATOR_NAMESPACE: str = "validator"  # GOAT Validator: direct access only
PROMOTER_NAMESPACE: str = "promoter"  # Memory Promoter: direct access only