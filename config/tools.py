"""Configuration constants for tools module.

This module centralizes all tool-related constants that were previously
scattered as magic numbers throughout tools/ files.

CONSTANTS:
==========
- MAX_FILE_SIZE: Maximum file size in bytes (default: 1 MB)
- MAX_SEARCH_RESULTS: Maximum results for search operations (default: 100)
- SHELL_TIMEOUT: Shell command timeout in seconds (default: 30)
- FILE_ALLOWED_EXTENSIONS: Set of allowed file extensions for text operations

USAGE:
======
    from config.tools import MAX_FILE_SIZE, FILE_ALLOWED_EXTENSIONS
"""
from __future__ import annotations

from typing import Final

# File operation limits
MAX_FILE_SIZE: Final[int] = 1 << 20  # 1 MB default
MAX_SEARCH_RESULTS: Final[int] = 100
SHELL_TIMEOUT: Final[int] = 30

# File extension allowlist for text operations
FILE_ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({
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
    ".lock",
    ".envrc",
})

__all__ = [
    "MAX_FILE_SIZE",
    "MAX_SEARCH_RESULTS",
    "SHELL_TIMEOUT",
    "FILE_ALLOWED_EXTENSIONS",
]