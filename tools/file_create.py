"""Create a new file within the workspace; fails if already exists unless exist_ok=true."""
from __future__ import annotations

from agents.base_agent import ToolDefinition
from tools.file_executor import EXECUTOR

__all__ = ["FILE_CREATE"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "File path relative to workspace root, or absolute (e.g. ~/Desktop/file.txt).",
        },
        "content": {
            "type": "string",
            "description": "Initial text content (default: empty string).",
        },
        "exist_ok": {
            "type": "boolean",
            "description": "When true, overwrite if the file already exists (default: false).",
        },
    },
    "required": ["path"],
}


async def _handler(path: str, content: str = "", exist_ok: bool = False) -> str:
    """Create a file; return 'OK: ...' or ERROR: <reason>."""
    target = EXECUTOR._resolve(path)
    if isinstance(target, str):
        return target
    if target.exists() and not exist_ok:
        return f"ERROR: file already exists (set exist_ok=true to overwrite): {path!r}"
    return EXECUTOR.write(path, content)


FILE_CREATE = ToolDefinition(
    name="file_create",
    description=(
        "Create a new file. Fails if already exists unless exist_ok=true. "
        "Parent directories are created automatically. "
        "Paths may be relative to workspace root or absolute."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
