"""BlobStore abstract base class (AC-518).

Backend-agnostic interface for large artifact storage. Implementations
must handle put/get/head/list/delete of opaque byte blobs keyed by
string paths.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
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
