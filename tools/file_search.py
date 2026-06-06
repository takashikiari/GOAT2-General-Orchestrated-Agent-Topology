"""Find files by name pattern within the workspace."""
from __future__ import annotations

import fnmatch

from agents.base_agent import ToolDefinition
from tools.file_executor import EXECUTOR

__all__ = ["FILE_SEARCH"]

MAX_SEARCH_RESULTS: int = 100

_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob pattern to match file names (e.g. '*.py', 'test_*', 'config.*').",
        },
        "path": {
            "type": "string",
            "description": "Directory to search within, relative to workspace root (default: '.').",
        },
        "limit": {
            "type": "integer",
            "description": f"Maximum results to return (default: {MAX_SEARCH_RESULTS}).",
        },
    },
    "required": ["pattern"],
}


async def _handler(pattern: str, path: str = ".", limit: int = MAX_SEARCH_RESULTS) -> str:
    """Find files matching pattern; return relative paths or ERROR: <reason>."""
    root = EXECUTOR._resolve(path)
    if isinstance(root, str):
        return root
    if not root.is_dir():
        return f"ERROR: not a directory: {path!r}"

    matches: list[str] = []
    try:
        for entry in root.rglob("*"):
            if entry.is_file() and fnmatch.fnmatch(entry.name, pattern):
                matches.append(str(entry.relative_to(root)))
                if len(matches) >= limit:
                    break
    except PermissionError as e:
        return f"ERROR: {e}"

    if not matches:
        return f"No files matching {pattern!r} found under {path!r}"

    result = "\n".join(matches)
    if len(matches) >= limit:
        result += f"\n... (limited to {limit} results)"
    return result


FILE_SEARCH = ToolDefinition(
    name="file_search",
    description=(
        "Search for files by name pattern (glob) within the workspace. "
        "Supports wildcards: '*.py', 'test_*', 'config.*'. "
        "Returns matching paths relative to the search root, one per line. "
        "Returns ERROR: <reason> on failure — never guess file locations."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
