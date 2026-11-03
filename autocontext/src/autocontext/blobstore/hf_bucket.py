"""Hugging Face Bucket blob store backend (AC-518).

Wraps ``huggingface-cli`` for upload/download. Uses a local cache
directory for hydrated blobs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from autocontext.blobstore.store import BlobStore, normalize_blob_key, prefix_matches, resolve_blob_path
from autocontext.offline import require_online

logger = logging.getLogger(__name__)
_INDEX_KEY = ".autocontext/blob_index.json"


class _HfEntryNotFoundError(RuntimeError):
    """The CLI positively identified a missing file, not an unreadable repo."""


class HfBucketStore(BlobStore):
    """HF Buckets backend using huggingface-cli."""

    def __init__(self, repo_id: str, cache_dir: Path, repo_type: str = "dataset") -> None:
        self.repo_id = repo_id
        self.cache_dir = cache_dir
        self.repo_type = repo_type
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        return self._put_with_index(key, data)

    def _put_with_index(
        self, key: str, data: bytes, index: dict[str, dict[str, Any]] | None = None
    ) -> str:
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
        if index is None:
            index = self._load_index()[1]
        index[normalized_key] = {
            "size_bytes": len(data),
            "digest": digest,
            "content_type": _guess_content_type(normalized_key),
        }
        self._save_index(index)
        return digest

    def append(self, key: str, data: bytes) -> str:
        """Append only after reading remote state successfully.

        Fresh download directories prevent a failed refresh from falling back
        to stale cached bytes. This read/modify/write operation still requires
        a single writer; HF uploads do not provide an atomic append primitive.
        """
        normalized_key = normalize_blob_key(key)
        with tempfile.TemporaryDirectory(prefix="hf-append-", dir=self.cache_dir) as tmp:
            download_dir = Path(tmp)
            index_bytes = self._download_for_append(_INDEX_KEY, download_dir)
            index: dict[str, dict[str, Any]] = {}
            if index_bytes is not None:
                try:
                    loaded = json.loads(index_bytes)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise RuntimeError("Cannot safely append with an unreadable HF blob index") from exc
                if not isinstance(loaded, dict) or any(
                    not isinstance(item_key, str) or not isinstance(metadata, dict)
                    for item_key, metadata in loaded.items()
                ):
                    raise RuntimeError("Cannot safely append with an invalid HF blob index")
                index = loaded

            existing = self._download_for_append(normalized_key, download_dir)
            metadata = index.get(normalized_key)
            if metadata is not None:
                if existing is None:
                    raise RuntimeError(f"Indexed HF blob is missing: {normalized_key!r}")
                digest = "sha256:" + hashlib.sha256(existing).hexdigest()
                if metadata.get("digest") != digest or metadata.get("size_bytes") != len(existing):
                    raise RuntimeError(f"HF blob does not match its index: {normalized_key!r}")

            # Only a confirmed missing file can initialize a new key. An absent
            # index is also valid on the first append into an empty repository.
            return self._put_with_index(normalized_key, (existing or b"") + data, index)

    def _download_for_append(self, key: str, download_dir: Path) -> bytes | None:
        path = resolve_blob_path(download_dir, key)
        try:
            self._run_hf_command(
                [
                    "huggingface-cli",
                    "download",
                    self.repo_id,
                    key,
                    "--repo-type",
                    self.repo_type,
                    "--local-dir",
                    str(download_dir),
                ]
            )
        except _HfEntryNotFoundError:
            return None
        if not path.is_file():
            raise RuntimeError(f"HF blob download completed without producing {key!r}")
        return path.read_bytes()

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
                    str(self.cache_dir),
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
                        str(self.cache_dir),
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
        require_online("synchronize Hugging Face blob storage", detail=self.repo_id)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            # A generic 404 can mean a private/inaccessible repository. Only
            # the CLI's final typed entry-not-found error establishes absence.
            # Unknown CLI error formats fail closed instead of risking a write.
            errors = re.findall(r"(?m)^([A-Za-z_][\w.]*):", result.stderr)
            if (
                cmd[1] == "download"
                and errors
                and errors[-1].startswith("huggingface_hub.")
                and errors[-1].rsplit(".", 1)[-1] in {"EntryNotFoundError", "RemoteEntryNotFoundError"}
            ):
                raise _HfEntryNotFoundError(f"HF file not found: {cmd[3]}")
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
