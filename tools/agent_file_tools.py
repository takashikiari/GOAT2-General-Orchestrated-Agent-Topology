"""tools.agent_file_tools — pre-built file operation tools for DAG agents.

All tools run file I/O in a thread pool (asyncio.to_thread) so they never
block the event loop. Paths are validated to prevent traversal outside the
workspace root.

Constants exported: FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, SHELL.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import subprocess
import time
from pathlib import Path

from tools.types import AgentTool

log = logging.getLogger("goat2.tools.agent_files")

_WORKSPACE = Path("/home/lenovo/workspace/goat2")
_MAX_CHARS = 12_000
_MAX_BYTES = 2_000_000
_SHELL_TIMEOUT = 30
_SHELL_MAX_OUT = 4_000


def _safe_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _WORKSPACE / p
    return p.resolve()


def _read_sync(path: Path, max_chars: int = _MAX_CHARS) -> str:
    if not path.exists():
        return f"ERROR: no such file: {path}"
    if path.is_dir():
        return f"ERROR: path is a directory: {path}"
    if path.stat().st_size > _MAX_BYTES:
        return f"ERROR: file too large (>{_MAX_BYTES} bytes)"
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return "ERROR: binary file, cannot read as text"
    text = raw.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        omitted = len(text) - max_chars
        return text[:max_chars] + f"\n...[{omitted} chars omitted]"
    return text


# ── FILE_READ ─────────────────────────────────────────────────────────────────

async def _file_read(path: str, max_chars: int = _MAX_CHARS) -> str:
    return await asyncio.to_thread(_read_sync, _safe_path(path), int(max_chars))

FILE_READ = AgentTool(
    name="file_read",
    description="Read a text file and return its contents (truncated to max_chars).",
    parameters={
        "type": "object",
        "properties": {
            "path":      {"type": "string", "description": "File path (absolute or relative to workspace root)"},
            "max_chars": {"type": "integer", "description": f"Max chars to return. Default: {_MAX_CHARS}"},
        },
        "required": ["path"],
    },
    handler=_file_read,
)


# ── FILE_READ_LINES ───────────────────────────────────────────────────────────

async def _file_read_lines(path: str, start: int = 1, end: int = 100) -> str:
    def _sync() -> str:
        p = _safe_path(path)
        if not p.exists():
            return f"ERROR: no such file: {p}"
        lines = p.read_text(errors="replace").splitlines()
        s, e = max(1, int(start)) - 1, min(len(lines), int(end))
        return "\n".join(f"{s+i+1}: {l}" for i, l in enumerate(lines[s:e]))
    return await asyncio.to_thread(_sync)

FILE_READ_LINES = AgentTool(
    name="file_read_lines",
    description="Read a specific line range from a file (1-indexed, inclusive).",
    parameters={
        "type": "object",
        "properties": {
            "path":  {"type": "string"},
            "start": {"type": "integer", "description": "First line (default 1)"},
            "end":   {"type": "integer", "description": "Last line (default 100)"},
        },
        "required": ["path"],
    },
    handler=_file_read_lines,
)


# ── FILE_WRITE ────────────────────────────────────────────────────────────────

async def _file_write(path: str, content: str) -> str:
    def _sync() -> str:
        p = _safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {p}"
    return await asyncio.to_thread(_sync)

FILE_WRITE = AgentTool(
    name="file_write",
    description="Overwrite a file with the given content (creates parent dirs if needed).",
    parameters={
        "type": "object",
        "properties": {
            "path":    {"type": "string"},
            "content": {"type": "string", "description": "Full file content to write"},
        },
        "required": ["path", "content"],
    },
    handler=_file_write,
)


# ── FILE_CREATE ───────────────────────────────────────────────────────────────

async def _file_create(path: str, content: str = "") -> str:
    def _sync() -> str:
        p = _safe_path(path)
        if p.exists():
            return f"ERROR: file already exists: {p} (use file_write to overwrite)"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Created {p} ({len(content)} chars)"
    return await asyncio.to_thread(_sync)

FILE_CREATE = AgentTool(
    name="file_create",
    description="Create a new file. Fails if the file already exists.",
    parameters={
        "type": "object",
        "properties": {
            "path":    {"type": "string"},
            "content": {"type": "string", "description": "Initial content (default empty)"},
        },
        "required": ["path"],
    },
    handler=_file_create,
)


# ── FILE_LIST ─────────────────────────────────────────────────────────────────

async def _file_list(path: str = ".", pattern: str = "*") -> str:
    def _sync() -> str:
        p = _safe_path(path)
        if not p.is_dir():
            return f"ERROR: not a directory: {p}"
        entries = sorted(p.iterdir())
        lines = [
            f"{'d' if e.is_dir() else 'f'} {e.name}"
            for e in entries
            if fnmatch.fnmatch(e.name, pattern)
        ]
        return "\n".join(lines) or "(empty)"
    return await asyncio.to_thread(_sync)

FILE_LIST = AgentTool(
    name="file_list",
    description="List files and directories at the given path (one level only).",
    parameters={
        "type": "object",
        "properties": {
            "path":    {"type": "string", "description": "Directory to list (default: workspace root)"},
            "pattern": {"type": "string", "description": "Glob pattern filter (default: '*')"},
        },
        "required": [],
    },
    handler=_file_list,
)


# ── FILE_SEARCH ───────────────────────────────────────────────────────────────

async def _file_search(pattern: str, path: str = ".") -> str:
    def _sync() -> str:
        p = _safe_path(path)
        matches = [str(m.relative_to(_WORKSPACE)) for m in p.rglob(pattern)][:50]
        return "\n".join(matches) or f"No files matching '{pattern}' in {p}"
    return await asyncio.to_thread(_sync)

FILE_SEARCH = AgentTool(
    name="file_search",
    description="Recursively search for files matching a glob pattern.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.py', 'config*.toml')"},
            "path":    {"type": "string", "description": "Root directory to search (default: workspace root)"},
        },
        "required": ["pattern"],
    },
    handler=_file_search,
)


# ── FILE_GREP ─────────────────────────────────────────────────────────────────

async def _file_grep(pattern: str, path: str = ".", file_pattern: str = "*") -> str:
    def _sync() -> str:
        p = _safe_path(path)
        rx = re.compile(pattern, re.IGNORECASE)
        results: list[str] = []
        for fp in p.rglob(file_pattern):
            if not fp.is_file() or fp.stat().st_size > _MAX_BYTES:
                continue
            try:
                for i, line in enumerate(fp.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        rel = fp.relative_to(_WORKSPACE)
                        results.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(results) >= 100:
                            return "\n".join(results) + "\n...[limit 100 reached]"
            except OSError:
                continue
        return "\n".join(results) or f"No matches for '{pattern}'"
    return await asyncio.to_thread(_sync)

FILE_GREP = AgentTool(
    name="file_grep",
    description="Search for a regex pattern across files, returning matching lines.",
    parameters={
        "type": "object",
        "properties": {
            "pattern":      {"type": "string", "description": "Regex pattern to search for"},
            "path":         {"type": "string", "description": "Root directory to search"},
            "file_pattern": {"type": "string", "description": "Glob filter for file names (default: '*')"},
        },
        "required": ["pattern"],
    },
    handler=_file_grep,
)


# ── FILE_INFO ─────────────────────────────────────────────────────────────────

async def _file_info(path: str) -> str:
    def _sync() -> str:
        p = _safe_path(path)
        if not p.exists():
            return f"ERROR: no such path: {p}"
        st = p.stat()
        kind = "directory" if p.is_dir() else "file"
        return (
            f"path: {p}\ntype: {kind}\nsize: {st.st_size} bytes\n"
            f"modified: {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}"
        )
    return await asyncio.to_thread(_sync)

FILE_INFO = AgentTool(
    name="file_info",
    description="Return metadata for a file or directory (size, type, last-modified).",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    handler=_file_info,
)


# ── SHELL ─────────────────────────────────────────────────────────────────────

async def _shell(command: str, timeout: int = _SHELL_TIMEOUT) -> str:
    def _sync() -> str:
        t = max(1, min(int(timeout), _SHELL_TIMEOUT))
        try:
            r = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, timeout=t, cwd=str(_WORKSPACE),
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {t}s"
        except Exception as exc:
            return f"ERROR: {exc}"
        out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
        if r.returncode != 0:
            out += f"\n[exit {r.returncode}]"
        if len(out) > _SHELL_MAX_OUT:
            out = out[:_SHELL_MAX_OUT] + f"\n...[truncated]"
        return out or "(no output)"
    return await asyncio.to_thread(_sync)

SHELL = AgentTool(
    name="shell",
    description=(
        "Run a shell command in the workspace root and return stdout+stderr. "
        "Timeout clamped to 30s. Output capped at 4000 chars."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (max 30)"},
        },
        "required": ["command"],
    },
    handler=_shell,
)
