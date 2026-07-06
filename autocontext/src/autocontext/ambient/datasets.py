"""per-target dataset files and manifests: curate's continuous output."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class DatasetManifest(BaseModel):
    """quality and freshness stats for one charter target's dataset.

    last_record_id doubles as curate's per-target cursor into the trace
    store, so the manifest write and the cursor advance are one atomic
    file replace.
    """

    model_config = ConfigDict(extra="forbid")

    target: str
    record_count: int = 0
    last_record_id: int = 0
    mean_score: float = 0.0
    quarantined_total: int = 0
    skipped_total: int = 0
    updated_at: str = ""


class DatasetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def dataset_path(self, target: str) -> Path:
        return self.root / f"{target}.jsonl"

    def _manifest_path(self, target: str) -> Path:
        return self.root / f"{target}.manifest.json"

    def load_manifest(self, target: str) -> DatasetManifest:
        path = self._manifest_path(target)
        if not path.exists():
            return DatasetManifest(target=target)
        return DatasetManifest(**json.loads(path.read_text(encoding="utf-8")))

    def save_manifest(self, manifest: DatasetManifest) -> None:
        # atomic replace: a crash mid-write must never leave a torn manifest,
        # because a reset manifest would rewind the cursor and duplicate records
        path = self._manifest_path(manifest.target)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest.model_dump(mode="json")), encoding="utf-8")
        os.replace(tmp, path)

    def append_records(self, target: str, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        with self.dataset_path(target).open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return len(records)

    def absorb(
        self,
        manifest: DatasetManifest,
        appended_scores: list[float],
        quarantined: int,
        skipped: int,
        last_record_id: int,
    ) -> DatasetManifest:
        new_count = manifest.record_count + len(appended_scores)
        if appended_scores:
            mean = (manifest.mean_score * manifest.record_count + sum(appended_scores)) / new_count
        else:
            mean = manifest.mean_score
        return DatasetManifest(
            target=manifest.target,
            record_count=new_count,
            last_record_id=last_record_id,
            mean_score=mean,
            quarantined_total=manifest.quarantined_total + quarantined,
            skipped_total=manifest.skipped_total + skipped,
            updated_at=datetime.now(UTC).isoformat(),
        )
