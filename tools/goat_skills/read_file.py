"""goat_skills.read_file — bounded host file-read tool, GOAT-only hot-reload plugin.

GOAT calls this when it needs to read a file from the host filesystem — config,
logs, source, notes. Safer and more focused than ``shell_run`` for plain reads:
no shell injection surface, a hard byte cap, binary detection, and a char
truncate. Like ``shell_run`` it is GOAT-only and NOT exposed to DAG agents.

Hot-reloaded by ``PluginManager.scan()`` — editing this file is picked up on the
next 30s reconcile without a restart.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools.read_file_config import (
    READ_FILE_DEFAULT_MAX_CHARS as _DEF_MAX_CHARS,
    READ_FILE_HARD_BYTE_CAP as _HARD_BYTE_CAP,
    READ_FILE_MAX_MAX_CHARS as _MAX_MAX_CHARS,
    READ_FILE_MIN_MAX_CHARS as _MIN_MAX_CHARS,
    READ_FILE_PATH_PREVIEW_CHARS as _PATH_PREVIEW,
)
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]

_DESCRIPTION = (
    "Read a file from the host filesystem and return its text contents. Use "
    "this for config, logs, source, notes — anything GOAT needs to inspect "
    f"locally. Output is truncated to max_chars (default {_DEF_MAX_CHARS}, "
    f"max {_MAX_MAX_CHARS}); for a big file read it in parts with start_line / end_line "
    "(1-based, inclusive; the reply says which lines it holds and how many the file has). "
    f"Files larger than {_HARD_BYTE_CAP // 1_000_000} MB "
    "are refused. Binary files are detected and refused. Relative paths resolve "
    "against the bot's working directory. GOAT-only — NOT available to DAG agents."
)


def _slice(text: str, start: int | None, end: int | None) -> str:
    """Lines start..end (1-based, inclusive) with a header saying which ones; the whole text when neither is given."""
    if start is None and end is None:
        return text
    lines = text.splitlines(keepends=True)
    total = len(lines)
    first = max(1, start or 1)
    last = min(total, end or total)
    if first > total or first > last:
        return f"ERROR: lines {start}-{end} are outside the file, which has {total} lines"
    return f"[lines {first}-{last} of {total}]\n" + "".join(lines[first - 1:last])


def _read(path: Path, max_chars: int, start: int | None = None, end: int | None = None) -> str:
    """Synchronous read, executed in a worker thread. Returns the content or an
    error string; never raises to the handler."""
    try:
        if not path.exists():
            return f"ERROR: no such file: {path}"
        if path.is_dir():
            return f"ERROR: path is a directory, not a file: {path}"
        size = path.stat().st_size
        if size > _HARD_BYTE_CAP:
            return (f"ERROR: file is {size} bytes (> {_HARD_BYTE_CAP} cap); "
                    f"read a slice via shell_run or narrow the path")
        raw = path.read_bytes()
    except PermissionError:
        return f"ERROR: permission denied: {path}"
    except OSError as exc:
        return f"ERROR: reading {path}: {exc}"
    if b"\x00" in raw[:8192]:
        return f"ERROR: binary file (null bytes detected), not text: {path}"
    # errors='replace' so a stray bad byte never fails the whole read.
    text = _slice(raw.decode("utf-8", errors="replace"), start, end)
    if len(text) > max_chars:
        omitted = len(text) - max_chars
        log.debug("read_file truncated path=%s chars=%d omitted=%d", path, max_chars, omitted)
        return text[:max_chars] + f"\n...[truncated {omitted} chars]"
    return text


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Return the read_file ToolDefinition (no registry deps needed)."""

    async def handler(path: str, max_chars: int = _DEF_MAX_CHARS, start_line: int | None = None,
                      end_line: int | None = None, chat_id: str = "") -> str:
        if not isinstance(path, str) or not path.strip():
            return "ERROR: empty path"
        mc = max(_MIN_MAX_CHARS, min(int(max_chars), _MAX_MAX_CHARS))
        try:
            start = int(start_line) if start_line not in (None, "") else None
            end = int(end_line) if end_line not in (None, "") else None
        except (TypeError, ValueError):
            return "ERROR: start_line and end_line must be whole numbers"
        p = Path(path).expanduser()
        log.info("read_file path=%s max_chars=%d lines=%s-%s", str(p)[:_PATH_PREVIEW], mc, start, end)
        content = await asyncio.to_thread(_read, p, mc, start, end)
        return content

    return [ToolDefinition(
        name="read_file",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to read (absolute, or relative to the bot's cwd).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Max chars to return, clamped to [{_MIN_MAX_CHARS}, {_MAX_MAX_CHARS}]. Default: {_DEF_MAX_CHARS}.",
                    "default": _DEF_MAX_CHARS,
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to return (1-based, inclusive). Use with end_line to read a big file in parts.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to return (1-based, inclusive). Default: the end of the file.",
                },
            },
            "required": ["path"],
        },
        handler=handler,
    )]