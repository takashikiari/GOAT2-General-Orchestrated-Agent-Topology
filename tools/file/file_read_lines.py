"""Read specific lines from a file (1-indexed). Useful for large files or code review."""
from __future__ import annotations

from agents.base_agent import ToolDefinition
from tools.file.file_executor import EXECUTOR

__all__ = ["FILE_READ_LINES"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "File path relative to workspace root, or absolute (e.g. ~/Desktop/file.txt). "
                "Sensitive files and paths outside workspace are blocked."
            ),
        },
        "start_line": {
            "type": "integer",
            "description": "First line number to read (1-indexed, default: 1).",
            "default": 1,
        },
        "end_line": {
            "type": "integer",
            "description": (
                "Last line number to read (inclusive, default: all remaining lines). "
                "Use this to read a specific range of lines."
            ),
            "default": None,
        },
    },
    "required": ["path"],
}


async def _handler(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read specific lines from a file; return text with line numbers or ERROR: <reason>."""
    return EXECUTOR.read_lines(path, start_line=start_line, end_line=end_line)


FILE_READ_LINES = ToolDefinition(
    name="file_read_lines",
    description=(
        "Read specific lines from a file (1-indexed). "
        "Returns lines with line numbers for easy reference. "
        "Useful for reading large files partially or reviewing specific sections of code. "
        "Paths may be relative to workspace root or absolute. "
        "Returns ERROR: <reason> on failure."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
