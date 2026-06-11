"""Get metadata about a file or directory (size, type, permissions, timestamps)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools._make_tool import make_tool
from tools.file.file_executor import EXECUTOR

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.file.info")

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
    log.debug("file_info: path=%r", path)
    return EXECUTOR.info(path)


FILE_INFO = make_tool(
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
