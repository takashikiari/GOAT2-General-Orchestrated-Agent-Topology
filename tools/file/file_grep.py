"""Search for a pattern within a file (case-insensitive substring match)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools._make_tool import make_tool
from tools.file.file_executor import EXECUTOR

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.file.grep")

__all__ = ["FILE_GREP"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "File path relative to workspace root, or absolute. "
                "Sensitive files and paths outside workspace are blocked."
            ),
        },
        "pattern": {
            "type": "string",
            "description": "Text pattern to search for (case-insensitive substring match).",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum matching lines to return (default: 50).",
            "default": 50,
        },
    },
    "required": ["path", "pattern"],
}


async def _handler(path: str, pattern: str, max_results: int = 50) -> str:
    """Search for pattern in file; return matching lines with numbers or ERROR: <reason>."""
    log.debug("file_grep: path=%r pattern=%r max_results=%d", path, pattern, max_results)
    return EXECUTOR.grep(path, pattern, max_results=max_results)


FILE_GREP = make_tool(
    name="file_grep",
    description=(
        "Search for a text pattern within a file (case-insensitive). "
        "Returns matching lines with line numbers. "
        "Useful for finding specific functions, variables, or content in files. "
        "Paths may be relative to workspace root or absolute. "
        "Returns ERROR: <reason> on failure."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
