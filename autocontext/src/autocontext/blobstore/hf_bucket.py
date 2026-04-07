"""Hugging Face Bucket blob store backend (AC-518).

Wraps ``huggingface-cli`` for upload/download. Uses a local cache
directory for hydrated blobs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from autocontext.blobstore.store import BlobStore, normalize_blob_key, prefix_matches, resolve_blob_path

logger = logging.getLogger(__name__)
_INDEX_KEY = ".autocontext/blob_index.json"


class HfBucketStore(BlobStore):
    """HF Buckets backend using huggingface-cli."""

    def __init__(self, repo_id: str, cache_dir: Path, repo_type: str = "dataset") -> None:
        self.repo_id = repo_id
        self.cache_dir = cache_dir
        self.repo_type = repo_type
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        normalized_key = normalize_blob_key(key)
        # Write to local cache first, then upload
        cache_path = resolve_blob_path(self.cache_dir, normalized_key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()

        self._run_hf_command(
            [
                "huggingface-cli",
                "upload",
                self.repo_id,
                str(cache_path),
                normalized_key,
                "--repo-type",
                self.repo_type,
            ]
        )
        index = self._load_index()[1]
        index[normalized_key] = {
            "size_bytes": len(data),
            "digest": digest,
            "content_type": _guess_content_type(normalized_key),
        }
        self._save_index(index)
        return digest

    def get(self, key: str) -> bytes | None:
        normalized_key = normalize_blob_key(key)
        # Try cache first
        cache_path = resolve_blob_path(self.cache_dir, normalized_key)
        if cache_path.is_file():
            return cache_path.read_bytes()

        index_available, index = self._load_index()
        if index_available and normalized_key not in index:
            return None

        # Download from remote
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._run_hf_command(
                [
                    "huggingface-cli",
                    "download",
                    self.repo_id,
                    normalized_key,
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
        normalized_key = normalize_blob_key(key)
        cache_path = resolve_blob_path(self.cache_dir, normalized_key)
        if cache_path.is_file():
            data = cache_path.read_bytes()
            return {
                "size_bytes": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "content_type": _guess_content_type(normalized_key),
            }
        index_available, index = self._load_index()
        if index_available:
            metadata = index.get(normalized_key)
            if metadata is not None:
                return dict(metadata)
        return None

    def list_prefix(self, prefix: str) -> list[str]:
        index_available, index = self._load_index()
        if index_available:
            return sorted(key for key in index if prefix_matches(key, prefix))

        # Fall back to local cache listing if index is unavailable.
        try:
            base = self.cache_dir / prefix.replace("\\", "/")
            parent = base.parent if not base.is_dir() else base
            if not parent.is_dir():
                return []
            return [
                p.relative_to(self.cache_dir).as_posix()
                for p in sorted(parent.rglob("*"))
                if p.is_file() and prefix_matches(p.relative_to(self.cache_dir).as_posix(), prefix)
            ]
        except Exception:
            return []

    def delete(self, key: str) -> bool:
        normalized_key = normalize_blob_key(key)
        cache_path = resolve_blob_path(self.cache_dir, normalized_key)
        index_available, index = self._load_index()
        existed = normalized_key in index if index_available else False
        if index_available and normalized_key in index:
            del index[normalized_key]
            self._save_index(index)
        self._delete_remote_file(normalized_key)
        if cache_path.is_file():
            cache_path.unlink()
            existed = True
        return existed

    def _load_index(self) -> tuple[bool, dict[str, dict[str, Any]]]:
        index_path = resolve_blob_path(self.cache_dir, _INDEX_KEY)
        if not index_path.is_file():
            try:
                self._run_hf_command(
                    [
                        "huggingface-cli",
                        "download",
                        self.repo_id,
                        _INDEX_KEY,
                        "--repo-type",
                        self.repo_type,
                        "--local-dir",
                        str(index_path.parent),
                    ]
                )
            except (RuntimeError, OSError):
                return False, {}
        if not index_path.is_file():
            return False, {}
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("failed to parse HF blob index for %s", self.repo_id)
            return False, {}
        if not isinstance(data, dict):
            return False, {}
        index: dict[str, dict[str, Any]] = {}
        for key, metadata in data.items():
            if isinstance(key, str) and isinstance(metadata, dict):
                index[key] = dict(metadata)
        return True, index

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        index_path = resolve_blob_path(self.cache_dir, _INDEX_KEY)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
        self._run_hf_command(
            [
                "huggingface-cli",
                "upload",
                self.repo_id,
                str(index_path),
                _INDEX_KEY,
                "--repo-type",
                self.repo_type,
            ]
        )

    def _delete_remote_file(self, key: str) -> None:
        try:
            self._run_hf_command(
                [
                    "huggingface-cli",
                    "repo-files",
                    "delete",
                    self.repo_id,
                    key,
                    "--repo-type",
                    self.repo_type,
                ]
            )
        except RuntimeError:
            logger.info("remote delete not available for %s in %s", key, self.repo_id)

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
