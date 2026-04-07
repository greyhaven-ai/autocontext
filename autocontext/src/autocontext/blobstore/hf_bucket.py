"""Hugging Face Bucket blob store backend (AC-518).

Wraps ``huggingface-cli`` for upload/download. Uses a local cache
directory for hydrated blobs.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Any

from autocontext.blobstore.store import BlobStore

logger = logging.getLogger(__name__)


class HfBucketStore(BlobStore):
    """HF Buckets backend using huggingface-cli."""

    def __init__(self, repo_id: str, cache_dir: Path, repo_type: str = "dataset") -> None:
        self.repo_id = repo_id
        self.cache_dir = cache_dir
        self.repo_type = repo_type
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        # Write to local cache first, then upload
        cache_path = self.cache_dir / key
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()

        self._run_hf_command(
            [
                "huggingface-cli",
                "upload",
                self.repo_id,
                str(cache_path),
                key,
                "--repo-type",
                self.repo_type,
            ]
        )
        return digest

    def get(self, key: str) -> bytes | None:
        # Try cache first
        cache_path = self.cache_dir / key
        if cache_path.is_file():
            return cache_path.read_bytes()

        # Download from remote
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._run_hf_command(
                [
                    "huggingface-cli",
                    "download",
                    self.repo_id,
                    key,
                    "--repo-type",
                    self.repo_type,
                    "--local-dir",
                    str(cache_path.parent),
                ]
            )
            if cache_path.is_file():
                return cache_path.read_bytes()
        except (RuntimeError, OSError):
            pass
        return None

    def head(self, key: str) -> dict[str, Any] | None:
        cache_path = self.cache_dir / key
        if cache_path.is_file():
            data = cache_path.read_bytes()
            return {
                "size_bytes": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "content_type": "application/octet-stream",
            }
        return None

    def list_prefix(self, prefix: str) -> list[str]:
        # List from local cache; remote listing would need HF API
        try:
            base = self.cache_dir / prefix
            parent = base.parent if not base.is_dir() else base
            if not parent.is_dir():
                return []
            return [
                str(p.relative_to(self.cache_dir))
                for p in sorted(parent.rglob("*"))
                if p.is_file() and str(p.relative_to(self.cache_dir)).startswith(prefix)
            ]
        except Exception:
            return []

    def delete(self, key: str) -> bool:
        cache_path = self.cache_dir / key
        if cache_path.is_file():
            cache_path.unlink()
            return True
        return False

    def _run_hf_command(self, cmd: list[str]) -> str:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"HF command failed: {result.stderr.strip()}")
        return result.stdout.strip()
