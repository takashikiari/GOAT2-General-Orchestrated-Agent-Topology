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
from tools.memory_count_tool import MEMORY_COUNT
from tools.memory_delete_tool import MEMORY_DELETE
from tools.memory_direct_query import MEMORY_DIRECT_QUERY
from tools.memory_export_tool import MEMORY_EXPORT
from tools.memory_last_write import MEMORY_LAST_WRITE
from tools.memory_temporal_tools import MEMORY_DEBUG_TRACE, MEMORY_RECENT, MEMORY_RECENT_DAG, MEMORY_TIMELINE
from tools.memory_tools import MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE
from tools.memory_tools import MEMORY_SEARCH_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG
from tools.memory_ttl_tool import MEMORY_TTL
from tools.memory_update_tool import MEMORY_UPDATE
from tools.memory_promote_tool import MEMORY_PROMOTE
from tools.memory_auto_promote_tool import MEMORY_AUTO_PROMOTE
from tools.memory_embedding_tool import MEMORY_EMBEDDING
from tools.shell_tool import SHELL
from tools.think import THINK
from tools.web_search import WEB_SEARCH
from agents.base_agent import ToolDefinition

__all__ = [
    "THINK", "CALCULATOR", "WEB_SEARCH", "SHELL",
    "FILE_READ", "FILE_WRITE", "FILE_CREATE", "FILE_LIST", "FILE_SEARCH",
    "FILE_GREP", "FILE_INFO", "FILE_READ_LINES",
    "MEMORY_SEARCH", "MEMORY_GET", "MEMORY_STORE", "MEMORY_DELETE", "MEMORY_UPDATE",
    "MEMORY_TIMELINE", "MEMORY_RECENT", "MEMORY_DEBUG_TRACE",
    "MEMORY_DIRECT_QUERY", "MEMORY_LAST_WRITE", "MEMORY_COUNT", "MEMORY_TTL", "MEMORY_EXPORT",
    "MEMORY_PROMOTE", "MEMORY_AUTO_PROMOTE", "MEMORY_EMBEDDING",
    "DAG_MEMORY_TOOLS",
    "ALL_TOOLS", "FILE_TOOLS", "MEMORY_TOOLS",
    "DAG_MEMORY_TOOLS",
]

ALL_TOOLS: list[ToolDefinition] = [
    THINK, CALCULATOR, WEB_SEARCH, SHELL,
    FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH,
    FILE_GREP, FILE_INFO, FILE_READ_LINES,
    MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE, MEMORY_DELETE, MEMORY_UPDATE,
    MEMORY_TIMELINE, MEMORY_RECENT, MEMORY_DEBUG_TRACE,
    MEMORY_DIRECT_QUERY, MEMORY_LAST_WRITE, MEMORY_COUNT, MEMORY_TTL, MEMORY_EMBEDDING, MEMORY_EXPORT,
]

FILE_TOOLS: list[ToolDefinition] = [
    FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
    FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES,
    WEB_SEARCH, SHELL,
]

MEMORY_TOOLS: list[ToolDefinition] = [
    MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE, MEMORY_DELETE, MEMORY_UPDATE,
    MEMORY_TIMELINE, MEMORY_RECENT, MEMORY_DEBUG_TRACE,
    MEMORY_DIRECT_QUERY, MEMORY_LAST_WRITE, MEMORY_COUNT, MEMORY_TTL, MEMORY_EMBEDDING, MEMORY_EXPORT,
    MEMORY_PROMOTE, MEMORY_AUTO_PROMOTE,
]

DAG_MEMORY_TOOLS: list[ToolDefinition] = [
    MEMORY_SEARCH_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_RECENT_DAG,
]
