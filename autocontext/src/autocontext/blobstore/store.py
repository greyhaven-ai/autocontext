"""BlobStore abstract base class (AC-518).

Backend-agnostic interface for large artifact storage. Implementations
must handle put/get/head/list/delete of opaque byte blobs keyed by
string paths.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


class BlobStore(ABC):
    """Abstract blob storage backend."""

    @abstractmethod
    def put(self, key: str, data: bytes) -> str:
        """Store bytes at key. Returns digest string (e.g. 'sha256:...')."""

    @abstractmethod
    def get(self, key: str) -> bytes | None:
        """Retrieve bytes by key. Returns None if not found."""

    @abstractmethod
    def head(self, key: str) -> dict[str, Any] | None:
        """Return metadata (size_bytes, digest, content_type) or None."""

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]:
        """List all keys matching a prefix."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if deleted, False if not found."""

    def put_file(self, key: str, path: Path) -> str:
        """Store a file at key. Default: read and delegate to put()."""
        return self.put(key, path.read_bytes())

    def get_file(self, key: str, dest: Path) -> bool:
        """Retrieve a blob to a file. Returns True on success."""
        data = self.get(key)
        if data is None:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True


def normalize_blob_key(key: str, *, allow_empty: bool = False) -> str:
    """Normalize a blob key and reject absolute or escaping paths."""
    if not key:
        if allow_empty:
            return ""
        raise ValueError("blob key must not be empty")

    for path_cls in (PurePosixPath, PureWindowsPath):
        candidate = path_cls(key)
        if candidate.is_absolute():
            raise ValueError(f"invalid blob key: {key!r}")

    normalized = key.replace("\\", "/")
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(f"invalid blob key: {key!r}")

    joined = "/".join(parts)
    if not joined and not allow_empty:
        raise ValueError("blob key must not be empty")
    return joined


def resolve_blob_path(root: Path, key: str) -> Path:
    """Resolve a normalized key under root and reject directory escapes."""
    normalized = normalize_blob_key(key)
    root_resolved = root.resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"invalid blob key: {key!r}") from exc
    return candidate


def prefix_matches(key: str, prefix: str) -> bool:
    """Return True if a normalized key matches a normalized prefix."""
    normalized_prefix = normalize_blob_key(prefix, allow_empty=True)
    if not normalized_prefix:
        return True
    if prefix.endswith(("/", "\\")):
        return key == normalized_prefix or key.startswith(normalized_prefix + "/")
    return key.startswith(normalized_prefix)
