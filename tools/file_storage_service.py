"""
FileStorageService — abstract storage backend + concrete implementations.

Implements the storage abstraction layer recommended by the research:
  - Abstract base class with read/write/delete/exists interface
  - LocalFileStorage: saves to local filesystem with path traversal protection
  - S3FileStorage: saves to S3-compatible object storage (optional)
  - Factory function to select backend via config

Usage:
    storage = get_storage_backend()  # reads FILE_STORAGE_BACKEND from env
    path = await storage.save("subdir/report.pdf", content_bytes)
    data = await storage.read("subdir/report.pdf")
    await storage.delete("subdir/report.pdf")
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional

log = logging.getLogger("goat2.file_storage")

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class FileStorageService(ABC):
    """
    Abstract file storage backend.

    All paths are *relative* logical keys (e.g. "uploads/user_123/report.pdf").
    The backend is responsible for mapping these to physical locations.
    """

    @abstractmethod
    async def save(
        self,
        key: str,
        content: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """
        Store content at *key*.

        Args:
            key:          Logical path (e.g. "uploads/uuid/file.pdf").
            content:      Raw bytes to store.
            content_type: MIME type hint (used by S3, ignored by local).
            metadata:     Optional key-value metadata (used by S3).

        Returns:
            The storage key on success.

        Raises:
            FileStorageError on failure.
        """
        ...

    @abstractmethod
    async def read(self, key: str) -> bytes:
        """
        Retrieve content stored at *key*.

        Returns:
            Raw bytes.

        Raises:
            FileNotFoundError if the key does not exist.
            FileStorageError on other failures.
        """
        ...

    @abstractmethod
    async def read_stream(self, key: str, chunk_size: int = 65536) -> BinaryIO:
        """
        Return a file-like object for streaming reads.

        Useful for large files where loading everything into memory is undesirable.

        Args:
            key:        Logical path.
            chunk_size: Recommended read chunk size (backend may ignore).

        Returns:
            A binary IO object (supports .read(chunk_size)).

        Raises:
            FileNotFoundError if the key does not exist.
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        Delete the stored object at *key*.

        Returns:
            True if deleted, False if the key did not exist.
        """
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if an object exists at *key*."""
        ...

    @abstractmethod
    async def size(self, key: str) -> int:
        """Return the size in bytes of the object at *key*."""
        ...

    @abstractmethod
    async def list_keys(self, prefix: str = "") -> list[str]:
        """
        List all keys under the given prefix.

        Args:
            prefix: Optional prefix filter (e.g. "uploads/").

        Returns:
            Sorted list of matching keys.
        """
        ...


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class FileStorageError(IOError):
    """Raised when a storage backend operation fails."""


# ---------------------------------------------------------------------------
# Local filesystem implementation
# ---------------------------------------------------------------------------

class LocalFileStorage(FileStorageService):
    """
    Stores files on the local filesystem under a configurable root directory.

    Security:
      - All keys are resolved relative to *root*.
      - Path traversal attacks (e.g. "../../etc/passwd") are blocked by
        canonical path resolution.
      - Atomic writes via tempfile + os.replace.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        log.info("LocalFileStorage root: %s", self._root)

    # ------------------------------------------------------------------
    # Path resolution (with traversal protection)
    # ------------------------------------------------------------------

    def _resolve(self, key: str) -> Path:
        """
        Resolve a logical key to an absolute Path under root.

        Raises FileStorageError on path traversal attempts.
        """
        # Normalise: strip leading slashes, reject absolute keys
        clean = key.lstrip("/\\")
        target = (self._root / clean).resolve()

        # Ensure the resolved path is still under root
        try:
            target.relative_to(self._root)
        except ValueError:
            raise FileStorageError(
                f"Path traversal blocked: {key!r} resolves outside storage root"
            ) from None

        return target

    # ------------------------------------------------------------------
    # Interface implementation
    # ------------------------------------------------------------------

    async def save(
        self,
        key: str,
        content: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Atomic write via tempfile + os.replace
            with tempfile.NamedTemporaryFile(
                dir=target.parent, delete=False, suffix=".tmp"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            os.replace(tmp_path, target)
            log.info("SAVED: %s (%d bytes)", target, len(content))
            return key
        except OSError as e:
            raise FileStorageError(f"Failed to save {key!r}: {e}") from e

    async def read(self, key: str) -> bytes:
        target = self._resolve(key)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {key!r} (resolved: {target})")
        try:
            return target.read_bytes()
        except OSError as e:
            raise FileStorageError(f"Failed to read {key!r}: {e}") from e

    async def read_stream(self, key: str, chunk_size: int = 65536) -> BinaryIO:
        target = self._resolve(key)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {key!r} (resolved: {target})")
        try:
            return open(target, "rb")
        except OSError as e:
            raise FileStorageError(f"Failed to open stream for {key!r}: {e}") from e

    async def delete(self, key: str) -> bool:
        target = self._resolve(key)
        if not target.exists():
            return False
        try:
            target.unlink()
            log.info("DELETED: %s", target)
            return True
        except OSError as e:
            raise FileStorageError(f"Failed to delete {key!r}: {e}") from e

    async def exists(self, key: str) -> bool:
        target = self._resolve(key)
        return target.is_file()

    async def size(self, key: str) -> int:
        target = self._resolve(key)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {key!r}")
        return target.stat().st_size

    async def list_keys(self, prefix: str = "") -> list[str]:
        base = self._resolve(prefix) if prefix else self._root
        if not base.is_dir():
            return []
        keys: list[str] = []
        for entry in sorted(base.rglob("*")):
            if entry.is_file():
                rel = entry.relative_to(self._root)
                keys.append(str(rel.as_posix()))
        return keys


# ---------------------------------------------------------------------------
# S3-compatible implementation (optional, requires boto3)
# ---------------------------------------------------------------------------

class S3FileStorage(FileStorageService):
    """
    Stores files in an S3-compatible object store.

    Requires the `boto3` package and the following environment variables:
      - S3_BUCKET (required)
      - S3_ENDPOINT_URL (optional, for MinIO / custom S3)
      - S3_REGION (default: us-east-1)
      - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or IAM role)
    """

    def __init__(
        self,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket or os.environ.get("S3_BUCKET", "")
        if not self._bucket:
            raise FileStorageError(
                "S3FileStorage requires S3_BUCKET environment variable"
            )

        self._endpoint = endpoint_url or os.environ.get("S3_ENDPOINT_URL")
        self._region = region or os.environ.get("S3_REGION", "us-east-1")

        try:
            import boto3
        except ImportError:
            raise FileStorageError(
                "S3FileStorage requires boto3: pip install boto3"
            ) from None

        kwargs: dict = {}
        if self._endpoint:
            kwargs["endpoint_url"] = self._endpoint
        self._client = boto3.client("s3", region_name=self._region, **kwargs)

        # Verify bucket exists
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception as e:
            log.warning("S3 bucket %r may not exist or is inaccessible: %s", self._bucket, e)

        log.info("S3FileStorage bucket=%s endpoint=%s", self._bucket, self._endpoint or "(AWS)")

    # ------------------------------------------------------------------
    # Interface implementation
    # ------------------------------------------------------------------

    async def save(
        self,
        key: str,
        content: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        extra: dict = {}
        if content_type:
            extra["ContentType"] = content_type
        if metadata:
            extra["Metadata"] = metadata

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                **extra,
            )
            log.info("S3 SAVED: %s (%d bytes)", key, len(content))
            return key
        except Exception as e:
            raise FileStorageError(f"S3 save failed for {key!r}: {e}") from e

    async def read(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except self._client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"S3 key not found: {key!r}") from None
        except Exception as e:
            raise FileStorageError(f"S3 read failed for {key!r}: {e}") from e

    async def read_stream(self, key: str, chunk_size: int = 65536) -> BinaryIO:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            # Return the StreamingBody directly (it's a file-like object)
            return resp["Body"]
        except self._client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"S3 key not found: {key!r}") from None
        except Exception as e:
            raise FileStorageError(f"S3 stream failed for {key!r}: {e}") from e

    async def delete(self, key: str) -> bool:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            log.info("S3 DELETED: %s", key)
            return True
        except Exception as e:
            raise FileStorageError(f"S3 delete failed for {key!r}: {e}") from e

    async def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client.exceptions.ClientError:
            return False

    async def size(self, key: str) -> int:
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
            return resp["ContentLength"]
        except self._client.exceptions.ClientError:
            raise FileNotFoundError(f"S3 key not found: {key!r}") from None

    async def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self._bucket, Prefix=prefix)
            for page in pages:
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        except Exception as e:
            raise FileStorageError(f"S3 list failed for prefix {prefix!r}: {e}") from e
        return sorted(keys)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_storage_backend() -> FileStorageService:
    """
    Factory: return the configured storage backend.

    Backend is selected by the ``FILE_STORAGE_BACKEND`` environment variable:
      - ``local`` (default) → :class:`LocalFileStorage`
      - ``s3``              → :class:`S3FileStorage`

    Local storage root is set via ``FILE_STORAGE_ROOT`` (default: ``./storage``).
    """
    backend = os.environ.get("FILE_STORAGE_BACKEND", "local").lower().strip()

    if backend == "s3":
        return S3FileStorage()

    # Default: local filesystem
    root = os.environ.get(
        "FILE_STORAGE_ROOT",
        str(Path.cwd() / "storage"),
    )
    return LocalFileStorage(root)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-initialised on first use)
# ---------------------------------------------------------------------------

_storage: FileStorageService | None = None


def get_storage() -> FileStorageService:
    """Return the global storage singleton, creating it if necessary."""
    global _storage
    if _storage is None:
        _storage = get_storage_backend()
    return _storage
