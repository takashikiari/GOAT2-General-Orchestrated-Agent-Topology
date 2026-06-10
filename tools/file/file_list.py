"""List directory contents within the workspace."""
from __future__ import annotations

from agents.base_agent import ToolDefinition
from tools.file.file_executor import EXECUTOR, MAX_LIST

__all__ = ["FILE_LIST"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Directory path relative to workspace root, or absolute. "
                "Sensitive paths (.git, __pycache__, .ssh) are blocked."
            ),
        },
        "limit": {
            "type": "integer",
            "description": f"Maximum entries to return (default: {MAX_LIST}).",
        },
    },
    "required": ["path"],
}


async def _handler(path: str, limit: int = MAX_LIST) -> str:
    """List directory; return 'f name' / 'd name' lines or ERROR: <reason>."""
    return EXECUTOR.list_dir(path, limit=limit)


FILE_LIST = ToolDefinition(
    name="file_list",
    description=(
        "List files and directories inside a workspace directory. "
        "Output: one entry per line, prefixed 'f' (file) or 'd' (directory). "
        "Paths may be relative to workspace root or absolute. "
        "Returns ERROR: <reason> on failure — never guess directory contents."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
