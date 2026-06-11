"""
Helper utilities for FileToolExecutor — format-aware parsing, timeout,
sensitive file patterns, and configuration constants.

Extracted from file_executor.py to keep each module under 200 lines.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import signal
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Iterator

log = logging.getLogger("goat2.tools.file.executor_helpers")

# ---------------------------------------------------------------------------
# Environment-based configuration
# ---------------------------------------------------------------------------

_WS: Final[Path] = Path(
    os.environ.get("GOAT_WORKSPACE") or Path(__file__).resolve().parent.parent
).resolve()

_ALLOW_OUTSIDE: Final[bool] = (
    os.environ.get("GOAT_ALLOW_OUTSIDE_WORKSPACE", "").lower() == "true"
)

_ALLOWED_PATHS: Final[frozenset[Path]] = frozenset(
    Path(p).expanduser().resolve()
    for p in os.environ.get("GOAT_ALLOWED_PATHS", "").split(":")
    if p.strip()
)

MAX_READ:  Final[int] = int(os.environ.get("FILE_READ_MAX_BYTES",  str(1 << 20)))       # 1 MB
MAX_WRITE: Final[int] = int(os.environ.get("FILE_WRITE_MAX_BYTES", str(1 << 20)))       # 1 MB
MAX_LIST:  Final[int] = int(os.environ.get("FILE_LIST_MAX_RESULTS", "200"))
TIMEOUT:   Final[int] = int(os.environ.get("FILE_OP_TIMEOUT_SECONDS", "30"))

# ---------------------------------------------------------------------------
# Sensitive file patterns (blocked from all operations)
# ---------------------------------------------------------------------------

_SENSITIVE_NAMES: Final[frozenset[str]] = frozenset({
    ".env", ".env.local", ".env.production", ".envrc",
    ".env.example", ".env.template",
    "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
    "id_rsa.pub", "id_ed25519.pub",
})

_SENSITIVE_EXTS: Final[frozenset[str]] = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".crt", ".ca-bundle",
    ".keystore", ".jks",
})

_SENSITIVE_PARTS: Final[frozenset[str]] = frozenset({
    ".git", "__pycache__", "secrets", ".ssh",
    "node_modules", ".venv", "venv", ".tox",
})

# ---------------------------------------------------------------------------
# Supported text file extensions (for format-aware reading)
# ---------------------------------------------------------------------------

SUPPORTED_TEXT_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".txt", ".md", ".markdown", ".rst",
    ".json", ".jsonl",
    ".csv", ".tsv",
    ".yaml", ".yml",
    ".toml",
    ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".c", ".cpp", ".h", ".hpp",
    ".rs", ".go", ".rb", ".php", ".swift",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".r", ".m", ".mm",
    ".cfg", ".ini", ".conf",
    ".log",
    ".tex", ".latex",
    ".css", ".scss", ".less",
    ".dockerfile", ".dockerignore",
    ".gitignore", ".gitattributes",
    ".editorconfig",
    ".toml", ".lock",
    ".envrc",
})

# ---------------------------------------------------------------------------
# Timeout helper
# ---------------------------------------------------------------------------

class TimeoutError(Exception):
    """Raised when a file operation exceeds the configured timeout."""


@contextmanager
def timeout_context(seconds: int) -> Iterator[None]:
    """
    Context manager that raises TimeoutError if the block takes too long.

    Uses signal.SIGALRM on Unix; logs a warning on Windows (no enforcement).
    """
    def _handler(signum: int, _frame: Any) -> None:
        raise TimeoutError(f"Operation timed out after {seconds}s")

    if hasattr(signal, "SIGALRM"):
        old = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
    else:
        if seconds > 0:
            log.warning("Timeout not supported on this platform (no SIGALRM)")
        yield


# ---------------------------------------------------------------------------
# Format-aware parsing helpers
# ---------------------------------------------------------------------------

def try_parse_json(content: str, path: str) -> str:
    """Try to parse content as JSON and return a pretty-printed representation."""
    try:
        data = json.loads(content)
        return json.dumps(data, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return content


def try_parse_csv(content: str, path: str) -> str:
    """Try to parse content as CSV and return a formatted Markdown table."""
    try:
        lines = content.strip().splitlines()
        if len(lines) < 2:
            return content
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return content
        headers = list(rows[0].keys())
        header_row = "| " + " | ".join(headers) + " |"
        sep_row    = "| " + " | ".join("---" for _ in headers) + " |"
        data_rows  = []
        for row in rows[:50]:
            data_rows.append("| " + " | ".join(row.get(h, "") for h in headers) + " |")
        if len(rows) > 50:
            data_rows.append(f"| *... {len(rows) - 50} more rows* |")
        return (
            f"```csv\n{content[:2000]}...\n```\n\n"
            f"**Parsed table ({len(rows)} rows):**\n\n"
            + "\n".join([header_row, sep_row] + data_rows)
        )
    except Exception:
        return content


def try_parse_xml(content: str, path: str) -> str:
    """Try to parse content as XML and return a formatted representation."""
    try:
        root = ET.fromstring(content)
        lines: list[str] = []

        def _walk(elem: ET.Element, indent: int = 0) -> None:
            prefix = "  " * indent
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            attrs = " ".join(f'{k}="{v}"' for k, v in elem.attrib.items())
            if attrs:
                lines.append(f"{prefix}<{tag} {attrs}>")
            else:
                lines.append(f"{prefix}<{tag}>")
            if elem.text and elem.text.strip():
                lines.append(f"{prefix}  {elem.text.strip()}")
            for child in elem:
                _walk(child, indent + 1)
            lines.append(f"{prefix}</{tag}>")

        _walk(root)
        return "\n".join(lines)
    except Exception:
        return content


_FORMAT_PARSERS: Final[dict[str, Any]] = {
    ".json": try_parse_json,
    ".jsonl": try_parse_json,
    ".csv": try_parse_csv,
    ".tsv": try_parse_csv,
    ".xml": try_parse_xml,
    ".html": try_parse_xml,
    ".htm": try_parse_xml,
}


def format_aware_read(content: str, path: str) -> str:
    """Apply format-aware parsing based on file extension."""
    ext = Path(path).suffix.lower()
    parser = _FORMAT_PARSERS.get(ext)
    if parser:
        try:
            return parser(content, path)
        except Exception as e:
            log.warning("Format parser failed for %s: %s", path, e)
    return content
