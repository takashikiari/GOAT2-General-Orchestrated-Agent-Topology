"""tools — GOAT's tool package.

Orchestrator tools (for the main GOAT bot) are built by factory functions:

    from tools.memory_tools import build_search_memory_tool

Agent tools (for DAG agents) are pre-built module-level constants:

    from tools import WEB_SEARCH, FILE_READ, MEMORY_STORE_DAG
"""

from tools.agent_file_tools import (
    FILE_READ,
    FILE_WRITE,
    FILE_CREATE,
    FILE_LIST,
    FILE_SEARCH,
    FILE_GREP,
    FILE_INFO,
    FILE_READ_LINES,
    SHELL,
    make_file_tools,
)
from tools.agent_dag_tools import (
    MEMORY_RECENT_DAG,
    MEMORY_GET_DAG,
    MEMORY_STORE_DAG,
    MEMORY_SEARCH_DAG,
)
from tools.agent_web_tools import FETCH_URL, WEB_SEARCH

__all__ = [
    # File tools (full workspace)
    "FILE_READ",
    "FILE_WRITE",
    "FILE_CREATE",
    "FILE_LIST",
    "FILE_SEARCH",
    "FILE_GREP",
    "FILE_INFO",
    "FILE_READ_LINES",
    "SHELL",
    "make_file_tools",
    # DAG memory tools
    "MEMORY_RECENT_DAG",
    "MEMORY_GET_DAG",
    "MEMORY_STORE_DAG",
    "MEMORY_SEARCH_DAG",
    # Web tools
    "WEB_SEARCH",
    "FETCH_URL",
]
