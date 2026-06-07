"""Tool registry — exports all tool definitions and convenience groupings.

This module aggregates every ToolDefinition from the tools package into
well-known lists (ALL_TOOLS, FILE_TOOLS, MEMORY_TOOLS) and re-exports
individual constants for direct import.
"""

from __future__ import annotations

from tools.calculator import CALCULATOR
from tools.file_create import FILE_CREATE
from tools.file_grep import FILE_GREP
from tools.file_info import FILE_INFO
from tools.file_list import FILE_LIST
from tools.file_read import FILE_READ
from tools.file_read_lines import FILE_READ_LINES
from tools.file_search import FILE_SEARCH
from tools.file_write import FILE_WRITE
from tools.memory_direct_query import MEMORY_DIRECT_QUERY
from tools.memory_last_write import MEMORY_LAST_WRITE
from tools.memory_temporal_tools import MEMORY_DEBUG_TRACE, MEMORY_RECENT, MEMORY_TIMELINE
from tools.memory_tools import MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE
from tools.think import THINK
from tools.web_search import WEB_SEARCH
from agents.base_agent import ToolDefinition

__all__ = [
    "THINK", "CALCULATOR", "WEB_SEARCH",
    "FILE_READ", "FILE_WRITE", "FILE_CREATE", "FILE_LIST", "FILE_SEARCH",
    "FILE_GREP", "FILE_INFO", "FILE_READ_LINES",
    "MEMORY_SEARCH", "MEMORY_GET", "MEMORY_STORE",
    "MEMORY_TIMELINE", "MEMORY_RECENT", "MEMORY_DEBUG_TRACE",
    "MEMORY_DIRECT_QUERY", "MEMORY_LAST_WRITE",
    "ALL_TOOLS", "FILE_TOOLS", "MEMORY_TOOLS",
    "DAG_MEMORY_TOOLS",
]

ALL_TOOLS: list[ToolDefinition] = [
    THINK, CALCULATOR, WEB_SEARCH,
    FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH,
    FILE_GREP, FILE_INFO, FILE_READ_LINES,
    MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE,
    MEMORY_TIMELINE, MEMORY_RECENT, MEMORY_DEBUG_TRACE,
    MEMORY_DIRECT_QUERY, MEMORY_LAST_WRITE,
]

FILE_TOOLS: list[ToolDefinition] = [
    FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
    FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES,
    WEB_SEARCH,
]

MEMORY_TOOLS: list[ToolDefinition] = [
    MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE,
    MEMORY_TIMELINE, MEMORY_RECENT, MEMORY_DEBUG_TRACE,
    MEMORY_DIRECT_QUERY, MEMORY_LAST_WRITE,
]

DAG_MEMORY_TOOLS: list[ToolDefinition] = [
    MEMORY_RECENT, MEMORY_GET,
]
