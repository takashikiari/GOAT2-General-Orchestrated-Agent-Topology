"""Get metadata about a file or directory (size, type, permissions, timestamps)."""
from __future__ import annotations

from agents.base_agent import ToolDefinition
from tools.file.file_executor import EXECUTOR

__all__ = ["FILE_INFO"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "File or directory path relative to workspace root, or absolute. "
                "Sensitive files and paths outside workspace are blocked."
            ),
        },
    },
    "required": ["path"],
}


async def _handler(path: str) -> str:
    """Return file/directory metadata or ERROR: <reason>."""
    return EXECUTOR.info(path)


FILE_INFO = ToolDefinition(
    name="file_info",
    description=(
        "Get metadata about a file or directory: name, path, type (file/directory), "
        "size, creation time, modification time, permissions, and entry count (for directories). "
        "Paths may be relative to workspace root or absolute. "
        "Returns ERROR: <reason> on failure."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
