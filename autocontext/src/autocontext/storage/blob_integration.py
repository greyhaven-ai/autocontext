"""Blob store integration for ArtifactStore writes (AC-518 Phase 3).

BlobAwareWriter wraps a BlobStore and mirrors large artifact writes
transparently. classify_artifact_kind maps file paths to blob kinds
for the BlobRef registry.
"""

from __future__ import annotations

from pathlib import Path

from autocontext.blobstore.ref import BlobRef
from autocontext.blobstore.store import BlobStore


class BlobAwareWriter:
    """Mirrors artifact writes to a BlobStore when enabled."""

    def __init__(
        self,
        blob_store: BlobStore | None,
        min_size_bytes: int = 1024,
    ) -> None:
        self._store = blob_store
        self._min_size = min_size_bytes

    def mirror_write(self, key: str, data: bytes, kind: str) -> BlobRef | None:
        """Mirror bytes to blob store. Returns BlobRef or None if disabled/too small."""
        if self._store is None:
            return None
        if len(data) < self._min_size:
            return None
        digest = self._store.put(key, data)
        return BlobRef(
            kind=kind,
            digest=digest,
            size_bytes=len(data),
            remote_uri=key,
        )

    def mirror_file(self, key: str, path: Path, kind: str) -> BlobRef | None:
        """Mirror a file to blob store. Returns BlobRef or None."""
        if self._store is None:
            return None
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size < self._min_size:
            return None
        digest = self._store.put_file(key, path)
        return BlobRef(
            kind=kind,
            digest=digest,
            size_bytes=size,
            local_path=str(path),
            remote_uri=key,
        )


def classify_artifact_kind(path: Path) -> str:
    """Classify an artifact file path into a blob kind."""
    name = path.name.lower()
    parts = str(path).lower()

    if "replay" in parts or "metrics" in name or name.endswith(".ndjson") or "event" in name:
        return "trace"
    if "playbook" in name or "analysis" in parts or "report" in name or "dead_end" in name:
        return "report"
    if "tools/" in parts and name.endswith(".py"):
        return "tool"
    if "checkpoint" in name or "model" in parts:
        return "checkpoint"
    if name.endswith(".jsonl") or "export" in parts or "training" in parts:
        return "export"
    return "artifact"
