"""Create a new file within the workspace; fails if already exists unless exist_ok=true."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools._make_tool import make_tool
from tools.file.file_executor import EXECUTOR

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.file.create")

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
    log.debug("file_create: path=%r exist_ok=%s len(content)=%d", path, exist_ok, len(content))
    target = EXECUTOR._resolve(path)
    if isinstance(target, str):
        log.warning("file_create: resolve failed for path=%r: %s", path, target)
        return target
    if target.exists() and not exist_ok:
        log.warning("file_create: file exists and exist_ok=False: %r", path)
        return f"ERROR: file already exists (set exist_ok=true to overwrite): {path!r}"
    return EXECUTOR.write(path, content)


FILE_CREATE = make_tool(
    name="file_create",
    description=(
        "Create a new file. Fails if already exists unless exist_ok=true. "
        "Parent directories are created automatically. "
        "Paths may be relative to workspace root or absolute."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
