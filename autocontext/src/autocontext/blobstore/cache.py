"""Hydration cache — local cache for remote-backed blobs (AC-518 Phase 2).

Provides digest-verified retrieval and LRU eviction when cache exceeds
the configured size budget.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from autocontext.blobstore.store import normalize_blob_key, resolve_blob_path

logger = logging.getLogger(__name__)


class HydrationCache:
    """Bounded local cache with digest verification."""

    def __init__(self, root: Path, max_mb: float = 500) -> None:
        self.root = root
        self.max_bytes = int(max_mb * 1024 * 1024)
        self.root.mkdir(parents=True, exist_ok=True)
        self._digests: dict[str, str] = {}  # key → digest

    def put(self, key: str, data: bytes, digest: str) -> None:
        """Cache data under key with associated digest."""
        normalized_key = normalize_blob_key(key)
        path = resolve_blob_path(self.root, normalized_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._digests[normalized_key] = digest
        self._evict_if_needed()

    def get(self, key: str, expected_digest: str | None = None) -> bytes | None:
        """Retrieve cached data. Verifies digest if provided."""
        normalized_key = normalize_blob_key(key)
        path = resolve_blob_path(self.root, normalized_key)
        if not path.is_file():
            return None
        data = path.read_bytes()
        if expected_digest:
            actual = "sha256:" + hashlib.sha256(data).hexdigest()
            if actual != expected_digest:
                logger.warning("digest mismatch for %s: expected %s, got %s", key, expected_digest, actual)
                path.unlink(missing_ok=True)
                self._digests.pop(normalized_key, None)
                return None
        # Refresh recency so eviction behaves like an LRU cache.
        path.touch(exist_ok=True)
        return data

    def total_size_bytes(self) -> int:
        """Total bytes currently in cache."""
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def clear(self) -> None:
        """Remove all cached files."""
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
        self._digests.clear()

    def _evict_if_needed(self) -> None:
        """Evict oldest files until under budget."""
        if self.max_bytes <= 0:
            return
        current = self.total_size_bytes()
        if current <= self.max_bytes:
            return

        # Sort by mtime (oldest first) and evict
        files = []
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    files.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        files.sort()

        for _mtime, path in files:
            if current <= self.max_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                current -= size
                rel = str(path.relative_to(self.root))
                self._digests.pop(rel, None)
            except OSError:
                continue
