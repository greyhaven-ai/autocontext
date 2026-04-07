"""Local filesystem blob store — content-addressed by SHA256 (AC-518)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from autocontext.blobstore.store import BlobStore, prefix_matches, resolve_blob_path


class LocalBlobStore(BlobStore):
    """Content-addressed local filesystem backend.

    Blobs are stored at ``root/<key>`` and their SHA256 digest is
    computed on write. The same content stored under different keys
    will have the same digest but occupy separate files (simplicity
    over dedup for the local backend).
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        digest = _sha256(data)
        path = resolve_blob_path(self.root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return digest

    def get(self, key: str) -> bytes | None:
        path = resolve_blob_path(self.root, key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def head(self, key: str) -> dict[str, Any] | None:
        path = resolve_blob_path(self.root, key)
        if not path.is_file():
            return None
        data = path.read_bytes()
        return {
            "size_bytes": len(data),
            "digest": _sha256(data),
            "content_type": _guess_content_type(key),
        }

    def list_prefix(self, prefix: str) -> list[str]:
        prefix_path = self.root / prefix.replace("\\", "/")
        base = prefix_path.parent if not prefix_path.is_dir() else prefix_path
        if not base.is_dir():
            return []
        results: list[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                rel = path.relative_to(self.root).as_posix()
                if prefix_matches(rel, prefix):
                    results.append(rel)
        return results

    def delete(self, key: str) -> bool:
        path = resolve_blob_path(self.root, key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def put_file(self, key: str, path: Path) -> str:
        dest = resolve_blob_path(self.root, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(path), str(dest))
        return _sha256(dest.read_bytes())

    def get_file(self, key: str, dest: Path) -> bool:
        src = resolve_blob_path(self.root, key)
        if not src.is_file():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
        return True


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _guess_content_type(key: str) -> str:
    if key.endswith(".json"):
        return "application/json"
    if key.endswith(".ndjson"):
        return "application/x-ndjson"
    if key.endswith(".md"):
        return "text/markdown"
    if key.endswith(".txt"):
        return "text/plain"
    return "application/octet-stream"
