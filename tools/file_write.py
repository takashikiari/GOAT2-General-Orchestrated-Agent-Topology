"""Write content to a file within the workspace (atomic via tempfile + os.replace).

Supports:
  - Overwrite mode (default): replaces the file entirely
  - Append mode: adds content to the end of an existing file
  - Automatic parent directory creation
  - Size validation (MAX_WRITE)
"""
from __future__ import annotations

from agents.base_agent import ToolDefinition
from tools.file_executor import EXECUTOR, MAX_WRITE

__all__ = ["FILE_WRITE"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "File path relative to workspace root, or absolute (e.g. ~/Desktop/file.txt). "
                "Parent directories are created automatically."
            ),
        },
        "content": {
            "type": "string",
            "description": "Text content to write.",
        },
        "mode": {
            "type": "string",
            "enum": ["overwrite", "append"],
            "description": (
                "'overwrite' (default) — replaces the file entirely. "
                "'append' — adds content to the end of an existing file."
            ),
            "default": "overwrite",
        },
    },
    "required": ["path", "content"],
}


async def _handler(path: str, content: str, mode: str = "overwrite") -> str:
    """Write file atomically; return 'OK: wrote N bytes' or ERROR: <reason>."""
    return EXECUTOR.write(path, content, mode=mode)


FILE_WRITE = ToolDefinition(
    name="file_write",
    description=(
        "Write text to a file atomically (tempfile + os.replace). "
        f"Size limit: {MAX_WRITE} bytes ({(MAX_WRITE / 1024):.0f} KB). "
        "Creates parent directories and new files. "
        "Supports 'overwrite' (default) and 'append' modes. "
        "Sensitive files are blocked. Returns ERROR: <reason> on failure."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
