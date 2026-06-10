"""
FileToolExecutor — central security gateway for all GOAT file operations.

Implements the security recommendations from the research:
  - Path traversal prevention (canonical path resolution)
  - Sensitive file blocking (.env, .key, .pem, .git, etc.)
  - Size limits for read/write
  - Timeout for operations
  - Audit logging
  - Support for multiple text formats (JSON, CSV, YAML, TOML, Markdown, XML)
  - Chunking for large files
  - Partial reads (offset + limit)
"""
from __future__ import annotations

import datetime
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Final

from tools.file.file_executor_helpers import (
    MAX_LIST,
    MAX_READ,
    MAX_WRITE,
    TIMEOUT,
    _ALLOWED_PATHS,
    _ALLOW_OUTSIDE,
    _SENSITIVE_EXTS,
    _SENSITIVE_NAMES,
    _SENSITIVE_PARTS,
    _WS,
    SUPPORTED_TEXT_EXTENSIONS,
    TimeoutError,
    format_aware_read,
    timeout_context,
)

log = logging.getLogger("goat2.file_executor")

__all__ = [
    "FileToolExecutor", "EXECUTOR",
    "MAX_READ", "MAX_WRITE", "MAX_LIST",
    "SUPPORTED_TEXT_EXTENSIONS",
]


class FileToolExecutor:
    """
    Security gateway for all file operations.

    Resolves paths relative to the workspace, blocks sensitive files,
    enforces size limits, and provides atomic write operations.
    """

    # ------------------------------------------------------------------
    # Path resolution & security
    # ------------------------------------------------------------------

    def _resolve(self, raw: str) -> Path | str:
        """Resolve *raw* to an absolute Path inside the workspace.

        Returns the Path on success, or an error string on failure.
        """
        exp = Path(os.path.expandvars(os.path.expanduser(raw)))
        res = exp.resolve() if exp.is_absolute() else (_WS / exp).resolve()

        try:
            res.relative_to(_WS)
            return res
        except ValueError:
            pass

        if _ALLOW_OUTSIDE and any(
            res == a or res.is_relative_to(a) for a in _ALLOWED_PATHS
        ):
            return res

        return f"ERROR: path outside workspace: {raw!r}"

    def _block(self, p: Path) -> str | None:
        """Check if *p* is a sensitive file or path.

        Returns an error string if blocked, None otherwise.
        """
        name = p.name.lower()
        stem = p.stem.lower()
        suffix = p.suffix.lower()

        if name in _SENSITIVE_NAMES or stem in _SENSITIVE_NAMES:
            return f"ERROR: sensitive file blocked: {p.name!r}"
        if suffix in _SENSITIVE_EXTS:
            return f"ERROR: sensitive file blocked (extension {suffix!r}): {p.name!r}"
        if any(pt.lower() in _SENSITIVE_PARTS for pt in p.parts):
            return f"ERROR: sensitive path blocked: {p}"
        return None

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def read(
        self,
        raw: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        format_aware: bool = True,
    ) -> str:
        """Read a file and return its contents as UTF-8 text.

        Args:
            raw:          File path (relative or absolute).
            offset:       Byte offset to start reading from (default: 0).
            limit:        Maximum bytes to read (default: MAX_READ).
            format_aware: If True, apply format-specific parsing.

        Returns:
            File contents as a string, or an error message prefixed with 'ERROR:'.
        """
        p = self._resolve(raw)
        if isinstance(p, str):
            return p

        blocked = self._block(p)
        if blocked:
            return blocked

        if not p.is_file():
            return f"ERROR: not found or not a file: {raw!r}"

        file_size = p.stat().st_size
        effective_limit = limit if limit is not None else MAX_READ

        if file_size > MAX_READ and limit is None:
            return (
                f"ERROR: file too large ({file_size} B > {MAX_READ} B): {raw!r}. "
                f"Use limit= parameter to read up to {MAX_READ} B, or offset= to skip bytes."
            )

        if offset > file_size:
            return f"ERROR: offset ({offset}) exceeds file size ({file_size}): {raw!r}"

        try:
            with timeout_context(TIMEOUT):
                with open(p, "rb") as f:
                    if offset > 0:
                        f.seek(offset)
                    bytes_to_read = min(effective_limit, file_size - offset)
                    raw_bytes = f.read(bytes_to_read)

            content = raw_bytes.decode("utf-8", errors="replace")

            if offset > 0 or bytes_to_read < file_size:
                total_kb = file_size / 1024
                read_kb = bytes_to_read / 1024
                summary = (
                    f"<!-- File: {p.name} ({total_kb:.1f} KB total) -->\n"
                    f"<!-- Read: {read_kb:.1f} KB starting at byte {offset} -->\n"
                )
                if bytes_to_read < file_size:
                    summary += (
                        f"<!-- Use offset={offset + bytes_to_read} to read the next chunk -->\n"
                    )
                content = summary + content

            if format_aware:
                content = format_aware_read(content, raw)

            log.info("READ OK: %s (%d bytes)", p, bytes_to_read)
            return content

        except TimeoutError:
            return f"ERROR: read timed out after {TIMEOUT}s: {raw!r}"
        except PermissionError:
            return f"ERROR: permission denied: {raw!r}"
        except Exception as e:
            log.exception("Read failed: %s", raw)
            return f"ERROR: {e}"

    def read_lines(
        self,
        raw: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read specific lines from a file (1-indexed).

        Args:
            raw:        File path.
            start_line: First line number to read (1-indexed, default: 1).
            end_line:   Last line number to read (inclusive, default: all remaining).

        Returns:
            The requested lines as a string, or an error message.
        """
        p = self._resolve(raw)
        if isinstance(p, str):
            return p

        blocked = self._block(p)
        if blocked:
            return blocked

        if not p.is_file():
            return f"ERROR: not found or not a file: {raw!r}"

        try:
            with timeout_context(TIMEOUT):
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

            total_lines = len(lines)
            if start_line < 1:
                start_line = 1
            if end_line is None or end_line > total_lines:
                end_line = total_lines
            if start_line > total_lines:
                return f"ERROR: start_line ({start_line}) exceeds total lines ({total_lines})"

            selected = lines[start_line - 1 : end_line]
            result = "".join(selected)

            if len(selected) > 1:
                line_width = len(str(end_line))
                numbered = []
                for i, line in enumerate(selected, start=start_line):
                    numbered.append(f"{i:>{line_width}}| {line}")
                result = "\n".join(numbered)

            log.info("READ LINES OK: %s (lines %d-%d of %d)", p, start_line, end_line, total_lines)
            return result

        except TimeoutError:
            return f"ERROR: read timed out after {TIMEOUT}s: {raw!r}"
        except Exception as e:
            log.exception("Read lines failed: %s", raw)
            return f"ERROR: {e}"

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def write(
        self,
        raw: str,
        content: str,
        *,
        create_ok: bool = True,
        mode: str = "overwrite",
    ) -> str:
        """Write content to a file atomically (tempfile + os.replace).

        Args:
            raw:       File path (relative or absolute).
            content:   Text content to write.
            create_ok: If True, create parent directories and new files (default: True).
            mode:      'overwrite' (default) or 'append'.

        Returns:
            Success message or error string.
        """
        p = self._resolve(raw)
        if isinstance(p, str):
            return p

        blocked = self._block(p)
        if blocked:
            return blocked

        enc = content.encode("utf-8")
        if len(enc) > MAX_WRITE:
            return (
                f"ERROR: content too large ({len(enc)} B > {MAX_WRITE} B). "
                f"Maximum write size is {MAX_WRITE / 1024:.0f} KB."
            )

        if not p.exists() and not create_ok:
            return f"ERROR: not found — use file_create: {raw!r}"

        try:
            with timeout_context(TIMEOUT):
                p.parent.mkdir(parents=True, exist_ok=True)

                if mode == "append" and p.exists():
                    existing = p.read_bytes()
                    new_content = existing + enc
                    with tempfile.NamedTemporaryFile(
                        dir=p.parent, delete=False, suffix=".tmp"
                    ) as t:
                        t.write(new_content)
                        tmp = t.name
                    os.replace(tmp, p)
                    log.info("APPEND OK: %s (+%d bytes, total %d)", p, len(enc), len(new_content))
                    return f"OK: appended {len(enc)} bytes to {p.name} (total: {len(new_content)} bytes)"
                else:
                    with tempfile.NamedTemporaryFile(
                        dir=p.parent, delete=False, suffix=".tmp"
                    ) as t:
                        t.write(enc)
                        tmp = t.name
                    os.replace(tmp, p)
                    log.info("WRITE OK: %s (%d bytes)", p, len(enc))
                    return f"OK: wrote {len(enc)} bytes to {p.name}"

        except TimeoutError:
            return f"ERROR: write timed out after {TIMEOUT}s: {raw!r}"
        except PermissionError:
            return f"ERROR: permission denied: {raw!r}"
        except OSError as e:
            return f"ERROR: {e}"
        except Exception as e:
            log.exception("Write failed: %s", raw)
            return f"ERROR: {e}"

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def list_dir(self, raw: str, limit: int = MAX_LIST) -> str:
        """List files and directories inside a directory.

        Returns one entry per line, prefixed 'f' (file) or 'd' (directory).

        Args:
            raw:   Directory path.
            limit: Maximum entries to return (default: MAX_LIST).

        Returns:
            Formatted listing or error string.
        """
        p = self._resolve(raw)
        if isinstance(p, str):
            return p

        blocked = self._block(p)
        if blocked:
            return blocked

        if not p.is_dir():
            return f"ERROR: not a directory: {raw!r}"

        try:
            with timeout_context(TIMEOUT):
                items = sorted(p.iterdir())[:limit]

            if not items:
                return "(empty)"

            lines = []
            for e in items:
                prefix = "d " if e.is_dir() else "f "
                size = ""
                if e.is_file():
                    try:
                        sz = e.stat().st_size
                        if sz < 1024:
                            size = f" ({sz} B)"
                        elif sz < 1024 * 1024:
                            size = f" ({sz / 1024:.1f} KB)"
                        else:
                            size = f" ({sz / (1024 * 1024):.1f} MB)"
                    except OSError:
                        pass
                lines.append(f"{prefix}{e.name}{size}")

            total = len(list(p.iterdir()))
            if total > limit:
                lines.append(f"... and {total - limit} more entries (use limit= to increase)")

            log.info("LIST OK: %s (%d entries)", p, min(total, limit))
            return "\n".join(lines)

        except TimeoutError:
            return f"ERROR: list timed out after {TIMEOUT}s: {raw!r}"
        except PermissionError:
            return f"ERROR: permission denied: {raw!r}"
        except Exception as e:
            log.exception("List failed: %s", raw)
            return f"ERROR: {e}"

    # ------------------------------------------------------------------
    # File info / metadata
    # ------------------------------------------------------------------

    def info(self, raw: str) -> str:
        """Return metadata about a file or directory.

        Returns a formatted string with: name, path, type, size, modified, etc.
        """
        p = self._resolve(raw)
        if isinstance(p, str):
            return p

        blocked = self._block(p)
        if blocked:
            return blocked

        if not p.exists():
            return f"ERROR: not found: {raw!r}"

        try:
            stat = p.stat()
            is_dir = p.is_dir()
            size = stat.st_size
            modified = stat.st_mtime

            modified_str = datetime.datetime.fromtimestamp(
                modified, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")

            info_lines = [
                f"Name:     {p.name}",
                f"Path:     {p}",
                f"Type:     {'directory' if is_dir else 'file'}",
                f"Size:     {size} bytes ({size / 1024:.1f} KB)" if not is_dir else "-",
                f"Created:  {datetime.datetime.fromtimestamp(stat.st_ctime, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                f"Modified: {modified_str}",
                f"Mode:     {oct(stat.st_mode)[-3:]}",
            ]

            if is_dir:
                try:
                    entries = list(p.iterdir())
                    info_lines.append(f"Entries:  {len(entries)}")
                    files = sum(1 for e in entries if e.is_file())
                    dirs = sum(1 for e in entries if e.is_dir())
                    info_lines.append(f"  Files:  {files}")
                    info_lines.append(f"  Dirs:   {dirs}")
                except OSError:
                    pass
            else:
                ext = p.suffix.lower()
                info_lines.append(f"Extension: {ext if ext else '(none)'}")
                if ext in SUPPORTED_TEXT_EXTENSIONS:
                    info_lines.append("Encoding: UTF-8 text (detected)")

            return "\n".join(info_lines)

        except Exception as e:
            log.exception("Info failed: %s", raw)
            return f"ERROR: {e}"

    # ------------------------------------------------------------------
    # Search within files
    # ------------------------------------------------------------------

    def grep(self, raw: str, pattern: str, *, max_results: int = 50) -> str:
        """Search for a pattern within a file (simple substring match).

        Args:
            raw:         File path.
            pattern:     Substring to search for (case-insensitive).
            max_results: Maximum matching lines to return.

        Returns:
            Matching lines with line numbers, or an error string.
        """
        p = self._resolve(raw)
        if isinstance(p, str):
            return p

        blocked = self._block(p)
        if blocked:
            return blocked

        if not p.is_file():
            return f"ERROR: not found or not a file: {raw!r}"

        try:
            with timeout_context(TIMEOUT):
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

            pattern_lower = pattern.lower()
            matches = []
            for i, line in enumerate(lines, start=1):
                if pattern_lower in line.lower():
                    matches.append((i, line.rstrip("\n")))
                    if len(matches) >= max_results:
                        break

            if not matches:
                return f"No matches found for {pattern!r} in {p.name}"

            result_lines = [
                f"Found {len(matches)} match(es) for {pattern!r} in {p.name}:",
            ]
            for line_no, text in matches:
                if len(text) > 200:
                    text = text[:200] + "..."
                result_lines.append(f"  {line_no}: {text}")

            if len(matches) >= max_results:
                result_lines.append(f"... (limited to {max_results} results)")

            return "\n".join(result_lines)

        except TimeoutError:
            return f"ERROR: search timed out after {TIMEOUT}s: {raw!r}"
        except Exception as e:
            log.exception("Grep failed: %s", raw)
            return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

EXECUTOR: Final[FileToolExecutor] = FileToolExecutor()
