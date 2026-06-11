"""
Helper utilities for FileStorageService — custom exception, factory function,
and module-level singleton management.

Extracted from file_storage_service.py to keep each module under 200 lines.

ARCHITECTURE (routing + TYPE_CHECKING + Registry):
==================================================
``FileStorageService`` lives in tools.file.file_storage_service, which is
a sibling module. The TYPE_CHECKING import is local to the tools/ layer —
no cross-layer (supervisor/agents) imports here.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.file.file_storage_service import FileStorageService

log = logging.getLogger("goat2.tools.file.storage_helpers")

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
    # Lazy imports — keep helpers importable without forcing the storage
    # module to load (LocalFileStorage imports nothing heavy, but S3
    # requires boto3 at runtime).
    from tools.file.file_storage_service import LocalFileStorage, S3FileStorage

    backend = os.environ.get("FILE_STORAGE_BACKEND", "local").lower().strip()

    log.debug("storage_backend: env=%r selecting=%s", backend, backend)

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
