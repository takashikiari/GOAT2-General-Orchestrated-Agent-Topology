"""
Helper utilities for FileStorageService — custom exception, factory function,
and module-level singleton management.

Extracted from file_storage_service.py to keep each module under 200 lines.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from file_storage_service import FileStorageService

log = logging.getLogger("goat2.file_storage_helpers")

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class FileStorageError(IOError):
    """Raised when a storage backend operation fails."""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_storage_backend() -> "FileStorageService":
    """
    Return the configured storage backend.

    Backend is selected by the ``FILE_STORAGE_BACKEND`` environment variable:
      - ``local`` (default) → :class:`LocalFileStorage`
      - ``s3``              → :class:`S3FileStorage`

    Local storage root is set via ``FILE_STORAGE_ROOT`` (default: ``./storage``).
    """
    # Lazy imports to avoid circular dependency
    from file_storage_service import LocalFileStorage, S3FileStorage

    backend = os.environ.get("FILE_STORAGE_BACKEND", "local").lower().strip()

    if backend == "s3":
        return S3FileStorage()

    root = os.environ.get(
        "FILE_STORAGE_ROOT",
        str(Path.cwd() / "storage"),
    )
    return LocalFileStorage(root)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-initialised on first use)
# ---------------------------------------------------------------------------

_storage: "FileStorageService | None" = None


def get_storage() -> "FileStorageService":
    """Return the global storage singleton, creating it if necessary."""
    global _storage
    if _storage is None:
        _storage = get_storage_backend()
    return _storage
