"""File operation tools — read, write, list, search files within workspace.

This module provides secure file operations through FileToolExecutor:
- Path safety: workspace root, symlink escape prevention, dotdot blocking
- Sensitive file blocking: .env, .key, .pem, .git, etc.
- Size limits: configurable read/write limits
- Atomic writes: tempfile + os.replace for crash safety
- Format-aware parsing: JSON, CSV, XML, YAML, Markdown

TOOL EXPORTS:
============
- FILE_READ: Read file contents (up to MAX_READ bytes)
- FILE_WRITE: Write file contents atomically
- FILE_CREATE: Create new file (fails if exists)
- FILE_LIST: List directory contents
- FILE_SEARCH: Find files by glob pattern
- FILE_GREP: Search within files
- FILE_INFO: Get file/directory metadata
- FILE_READ_LINES: Read specific line range

EXPORTS ALSO:
===========
- FileToolExecutor: Central security gateway class
- MAX_READ: Maximum bytes for read operations (default: 1 MB)
- MAX_WRITE: Maximum bytes for write operations (default: 1 MB)
- MAX_LIST: Maximum entries for directory listing (default: 200)
- SUPPORTED_TEXT_EXTENSIONS: Set of supported text file extensions
"""

from __future__ import annotations

from tools.file.file_create import FILE_CREATE
from tools.file.file_executor import EXECUTOR, FileToolExecutor
from tools.file.file_executor import MAX_READ, MAX_WRITE, MAX_LIST
from tools.file.file_executor import SUPPORTED_TEXT_EXTENSIONS
from tools.file.file_grep import FILE_GREP
from tools.file.file_info import FILE_INFO
from tools.file.file_list import FILE_LIST
from tools.file.file_read import FILE_READ
from tools.file.file_read_lines import FILE_READ_LINES
from tools.file.file_search import FILE_SEARCH
from tools.file.file_write import FILE_WRITE

__all__ = [
    # Tool definitions
    "FILE_READ",
    "FILE_WRITE",
    "FILE_CREATE",
    "FILE_LIST",
    "FILE_SEARCH",
    "FILE_GREP",
    "FILE_INFO",
    "FILE_READ_LINES",
    # Executor and constants
    "FileToolExecutor",
    "EXECUTOR",
    "MAX_READ",
    "MAX_WRITE",
    "MAX_LIST",
    "SUPPORTED_TEXT_EXTENSIONS",
]