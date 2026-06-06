"""Read a file within the workspace and return its UTF-8 contents.

Supports:
  - Full file reads (up to MAX_READ bytes)
  - Partial reads with offset and limit
  - Line-based reads (read_lines)
  - Format-aware parsing (JSON, CSV, XML, etc.)
  - Search within files (grep)
"""
from __future__ import annotations

from agents.base_agent import ToolDefinition
from tools.file_executor import EXECUTOR, MAX_READ

__all__ = ["FILE_READ"]

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
        "offset": {
            "type": "integer",
            "description": (
                "Byte offset to start reading from (default: 0). "
                "Use this to read large files in chunks."
            ),
            "default": 0,
        },
        "limit": {
            "type": "integer",
            "description": (
                f"Maximum bytes to read (default: {MAX_READ}, max: {MAX_READ}). "
                "Use with offset= to read specific portions of a file."
            ),
            "default": MAX_READ,
        },
        "format_aware": {
            "type": "boolean",
            "description": (
                "If true (default), apply format-specific parsing: "
                "JSON is pretty-printed, CSV is shown as a table, XML is formatted. "
                "Set to false for raw text."
            ),
            "default": True,
        },
    },
    "required": ["path"],
}


async def _handler(
    path: str,
    offset: int = 0,
    limit: int | None = None,
    format_aware: bool = True,
) -> str:
    """Read a file; return UTF-8 text or ERROR: <reason>. If unavailable: 'tool not connected'."""
    return EXECUTOR.read(path, offset=offset, limit=limit, format_aware=format_aware)


FILE_READ = ToolDefinition(
    name="file_read",
    description=(
        "Read a file and return its UTF-8 contents. "
        f"Size limit: {MAX_READ} bytes ({(MAX_READ / 1024):.0f} KB). "
        "For large files, use offset= and limit= to read in chunks. "
        "Paths may be relative to workspace root or absolute. "
        "Returns ERROR: <reason> on failure — never hallucinate file contents."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
