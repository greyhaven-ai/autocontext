"""BlobStore factory (AC-518)."""

from __future__ import annotations

from pathlib import Path

from autocontext.blobstore.store import BlobStore


def create_blob_store(
    backend: str,
    root: str = "",
    repo_id: str = "",
    cache_dir: str = "",
    **kwargs: object,
) -> BlobStore:
    """Create a BlobStore backend from configuration.

    Args:
        backend: "local" or "hf_bucket"
        root: Root directory for local backend
        repo_id: HF repo ID for hf_bucket backend
        cache_dir: Local cache directory for hf_bucket backend
    """
    if backend == "local":
        from autocontext.blobstore.local import LocalBlobStore

        return LocalBlobStore(root=Path(root))

    if backend == "hf_bucket":
        from autocontext.blobstore.hf_bucket import HfBucketStore

        return HfBucketStore(
            repo_id=repo_id,
            cache_dir=Path(cache_dir) if cache_dir else Path(root) / ".hf_cache",
        )

    raise ValueError(f"Unknown blob store backend: {backend!r}. Available: 'local', 'hf_bucket'")
