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
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]

# Char return cap (model-facing, clamped). Default chosen to fit comfortably in
# context without crowding out memory tiers; raised on demand up to the hard cap.
_DEF_MAX_CHARS = 8000
_MIN_MAX_CHARS, _MAX_MAX_CHARS = 100, 100_000
# Hard byte cap: refuse files larger than this outright so a multi-GB file can't
# be loaded into memory or the loop. 2 MB is well past any useful single read.
_HARD_BYTE_CAP = 2_000_000
_PATH_PREVIEW = 120

_DESCRIPTION = (
    "Read a file from the host filesystem and return its text contents. Use "
    "this for config, logs, source, notes — anything GOAT needs to inspect "
    "locally. Output is truncated to max_chars (default 8000, max 100000); "
    "files larger than 2 MB are refused. Binary files are detected and refused. "
    "Relative paths resolve against the bot's working directory. GOAT-only — "
    "NOT available to DAG agents."
)


def _read(path: Path, max_chars: int) -> str:
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
    text = raw.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        omitted = len(text) - max_chars
        log.debug("read_file truncated path=%s chars=%d omitted=%d", path, max_chars, omitted)
        return text[:max_chars] + f"\n...[truncated {omitted} chars]"
    return text


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Return the read_file ToolDefinition (no registry deps needed)."""

    async def handler(path: str, max_chars: int = _DEF_MAX_CHARS, chat_id: str = "") -> str:
        if not isinstance(path, str) or not path.strip():
            return "ERROR: empty path"
        mc = max(_MIN_MAX_CHARS, min(int(max_chars), _MAX_MAX_CHARS))
        p = Path(path).expanduser()
        log.info("read_file path=%s max_chars=%d", str(p)[:_PATH_PREVIEW], mc)
        content = await asyncio.to_thread(_read, p, mc)
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
                    "description": f"Max chars to return, clamped to [100, 100000]. Default: {_DEF_MAX_CHARS}.",
                    "default": _DEF_MAX_CHARS,
                },
            },
            "required": ["path"],
        },
        handler=handler,
    )]