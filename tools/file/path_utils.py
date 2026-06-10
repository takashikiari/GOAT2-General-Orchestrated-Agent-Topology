"""Backward-compatibility shim — delegates to tools.file.file_executor.

Import safe_path, WORKSPACE, or ALLOW_OUTSIDE from here if existing callers
need them; new code should use tools.file.file_executor.EXECUTOR directly.
"""
from __future__ import annotations

from pathlib import Path

from tools.file.file_executor import EXECUTOR
from tools.file.file_executor import _WS as WORKSPACE
from tools.file.file_executor import _ALLOW_OUTSIDE as ALLOW_OUTSIDE

__all__ = ["WORKSPACE", "ALLOW_OUTSIDE", "safe_path"]


def safe_path(raw: str) -> Path | None:
    """Resolve raw path through the executor; return Path or None on error."""
    result = EXECUTOR._resolve(raw)
    return result if isinstance(result, Path) else None
