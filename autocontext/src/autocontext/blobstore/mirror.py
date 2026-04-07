"""BlobMirror — hooks artifact writes into blob store (AC-518 Phase 2).

Intercepts large artifact writes and mirrors them to the configured
BlobStore backend. Optionally registers BlobRefs in a BlobRegistry
for later lookup.
"""

from __future__ import annotations

from pathlib import Path

from autocontext.blobstore.ref import BlobRef
from autocontext.blobstore.store import BlobStore


class BlobMirror:
    """Mirrors artifacts to a BlobStore backend."""

    def __init__(
        self,
        store: BlobStore,
        min_size_bytes: int = 1024,
        registry: object | None = None,
    ) -> None:
        self.store = store
        self.min_size_bytes = min_size_bytes
        self._registry = registry

    def mirror_artifact(
        self,
        key: str,
        data: bytes,
        kind: str,
        run_id: str = "",
        artifact_name: str = "",
    ) -> BlobRef | None:
        """Mirror bytes to blob store. Returns BlobRef or None if too small."""
        if len(data) < self.min_size_bytes:
            return None

        digest = self.store.put(key, data)
        ref = BlobRef(
            kind=kind,
            digest=digest,
            size_bytes=len(data),
            local_path="",
            remote_uri=key,
        )

        if self._registry is not None and run_id and artifact_name:
            from autocontext.blobstore.registry import BlobRegistry

            if isinstance(self._registry, BlobRegistry):
                self._registry.register(run_id, artifact_name, ref)

        return ref

    def mirror_file(
        self,
        key: str,
        path: Path,
        kind: str,
        run_id: str = "",
        artifact_name: str = "",
    ) -> BlobRef | None:
        """Mirror a file to blob store. Returns BlobRef or None if too small."""
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size < self.min_size_bytes:
            return None

        digest = self.store.put_file(key, path)
        ref = BlobRef(
            kind=kind,
            digest=digest,
            size_bytes=size,
            local_path=str(path),
            remote_uri=key,
        )

        if self._registry is not None and run_id and artifact_name:
            from autocontext.blobstore.registry import BlobRegistry

            if isinstance(self._registry, BlobRegistry):
                self._registry.register(run_id, artifact_name, ref)

        return ref
