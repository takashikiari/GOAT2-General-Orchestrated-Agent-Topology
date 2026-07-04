"""goat_skills.write_file — bounded host file-write tool, GOAT-only hot-reload plugin.

GOAT calls this when it needs to write or append to a file on the host
filesystem — notes, config drafts, generated output. Safer and more focused
than ``shell_run`` for plain writes: no shell injection surface, a hard size
cap, parent-dir creation, and explicit overwrite/append modes. Like
``shell_run`` it is GOAT-only and NOT exposed to DAG agents; it does not widen
the trust boundary (``shell_run`` already permits unrestricted file writes),
only offers a narrower, cleaner path.

Hot-reloaded by ``PluginManager.scan()`` — editing this file is picked up on the
next 30s reconcile without a restart.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]

# Hard cap on content so a runaway write can't dump a huge file or pin the loop.
# 200k chars covers any reasonable single tool write; larger writes should stream
# via shell_run or be split across calls.
_MAX_CONTENT_CHARS = 200_000
_PATH_PREVIEW = 120
_MODES = ("overwrite", "append")

_DESCRIPTION = (
    "Write or append text to a file on the host filesystem. Use this for notes, "
    "config drafts, generated output — anything GOAT should persist locally. "
    "``mode`` is 'overwrite' (default, replaces the file) or 'append' (adds to "
    "the end, creating the file if absent). Parent directories are created by "
    "default (set create_dirs=false to disable). Content is capped at 200000 "
    "chars; writing to an existing directory path is refused. GOAT-only — NOT "
    "available to DAG agents."
)


def _write(path: Path, content: str, mode: str, create_dirs: bool) -> str:
    """Synchronous write, executed in a worker thread. Returns a status or error
    string; never raises to the handler."""
    try:
        if path.exists() and path.is_dir():
            return f"ERROR: path is a directory, not a file: {path}"
        parent = path.parent
        if not parent.exists():
            if not create_dirs:
                return f"ERROR: parent directory missing: {parent}"
            parent.mkdir(parents=True, exist_ok=True)
        elif not parent.is_dir():
            return f"ERROR: parent is not a directory: {parent}"
        data = content.encode("utf-8")
        if mode == "append" and path.exists():
            with path.open("ab") as f:
                f.write(data)
            total = path.stat().st_size
            log.debug("write_file append path=%s bytes=%d total=%d", path, len(data), total)
            return f"Appended {len(data)} bytes to {path} (now {total} bytes total)"
        # overwrite (also handles append-to-new-file): atomic temp + replace so a
        # crash mid-write can't leave a truncated/partial file at the target path.
        tmp = path.with_name(path.name + ".goat_write_tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        log.debug("write_file overwrite path=%s bytes=%d", path, len(data))
        return f"Wrote {len(data)} bytes to {path}"
    except PermissionError:
        return f"ERROR: permission denied: {path}"
    except OSError as exc:
        return f"ERROR: writing {path}: {exc}"


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Return the write_file ToolDefinition (no registry deps needed)."""

    async def handler(path: str, content: str, mode: str = "overwrite",
                      create_dirs: bool = True, chat_id: str = "") -> str:
        if not isinstance(path, str) or not path.strip():
            return "ERROR: empty path"
        if not isinstance(content, str):
            return "ERROR: content must be a string"
        if mode not in _MODES:
            return f"ERROR: mode must be one of {list(_MODES)}, got {mode!r}"
        if len(content) > _MAX_CONTENT_CHARS:
            return (f"ERROR: content is {len(content)} chars (> {_MAX_CONTENT_CHARS} cap); "
                    f"split the write or use shell_run")
        p = Path(path).expanduser()
        log.info("write_file path=%s mode=%s chars=%d", str(p)[:_PATH_PREVIEW], mode, len(content))
        return await asyncio.to_thread(_write, p, content, mode, create_dirs)

    return [ToolDefinition(
        name="write_file",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write (absolute, or relative to the bot's cwd).",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write (UTF-8).",
                },
                "mode": {
                    "type": "string",
                    "enum": list(_MODES),
                    "description": "'overwrite' replaces the file (default); 'append' adds to the end.",
                    "default": "overwrite",
                },
                "create_dirs": {
                    "type": "boolean",
                    "description": "Create parent directories if missing (default true).",
                    "default": True,
                },
            },
            "required": ["path", "content"],
        },
        handler=handler,
    )]