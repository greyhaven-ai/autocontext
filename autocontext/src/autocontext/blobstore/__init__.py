"""Deduplicated bucket-backed blob store (AC-518)."""

from __future__ import annotations

from autocontext.blobstore.factory import create_blob_store
from autocontext.blobstore.ref import BlobRef
from autocontext.blobstore.registry import BlobRegistry
from autocontext.blobstore.store import BlobStore

__all__ = [
    "BlobRef",
    "BlobRegistry",
    "BlobStore",
    "create_blob_store",
]
