"""SyncManager — bulk sync local runs to blob store (AC-518 Phase 2)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.blobstore.store import BlobStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncResult:
    """Outcome of syncing a run to blob store."""

    run_id: str
    synced_count: int
    skipped_count: int
    total_bytes: int
    errors: list[str]


class SyncManager:
    """Bulk sync local run artifacts to a BlobStore backend."""

    def __init__(self, store: BlobStore, runs_root: Path) -> None:
        self.store = store
        self.runs_root = runs_root

    def sync_run(self, run_id: str) -> SyncResult:
        """Sync all artifacts from a run directory to the blob store."""
        run_dir = self.runs_root / run_id
        if not run_dir.is_dir():
            return SyncResult(run_id=run_id, synced_count=0, skipped_count=0, total_bytes=0, errors=[])

        synced = 0
        skipped = 0
        total_bytes = 0
        errors: list[str] = []

        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            key = f"runs/{run_id}/{path.relative_to(run_dir)}"
            try:
                local_size = path.stat().st_size
                existing = self.store.head(key)
                if existing is not None and _matches_local_artifact(path, local_size, existing):
                    skipped += 1
                    continue

                self.store.put_file(key, path)
                synced += 1
                total_bytes += local_size
            except Exception as exc:
                errors.append(f"{key}: {exc}")

        return SyncResult(
            run_id=run_id,
            synced_count=synced,
            skipped_count=skipped,
            total_bytes=total_bytes,
            errors=errors,
        )

    def status(self) -> dict[str, Any]:
        """Return blob store status: total blobs, total bytes, run count."""
        keys = self.store.list_prefix("runs/")
        total_bytes = 0
        runs: set[str] = set()
        for key in keys:
            parts = key.split("/")
            if len(parts) >= 2:
                runs.add(parts[1])
            meta = self.store.head(key)
            if meta:
                total_bytes += meta.get("size_bytes", 0)

        return {
            "total_blobs": len(keys),
            "total_bytes": total_bytes,
            "synced_runs": sorted(runs),
            "run_count": len(runs),
        }


def _matches_local_artifact(path: Path, local_size: int, existing: dict[str, Any]) -> bool:
    """Return True when remote metadata matches the current local artifact."""
    if existing.get("size_bytes") != local_size:
        return False
    existing_digest = existing.get("digest")
    if not isinstance(existing_digest, str) or not existing_digest:
        return False
    return existing_digest == _file_sha256(path)


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
