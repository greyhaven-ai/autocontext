"""Crash-resilient, content-addressed artifacts for a kernel search lineage."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from autocontext.kernel_evolution._file_lock import append_bytes_locked
from autocontext.kernel_evolution.models import (
    KernelAttemptRecord,
    KernelBenchmarkObservation,
    KernelCandidate,
    KernelEvolutionResult,
    content_digest,
)
from autocontext.util.json_io import write_json, write_text_atomic


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    """Persist exact bytes without platform newline translation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


class KernelLineageStore:
    """Persist every proposal and decision without rewriting its history."""

    def __init__(self, root: Path, run_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) or ".." in run_id:
            raise ValueError("run_id must be a safe path segment")
        self.run_id = run_id
        self.run_dir = root / run_id
        if self.run_dir.exists() and any(self.run_dir.iterdir()):
            raise FileExistsError(f"kernel run directory is not empty; resume is not supported: {self.run_dir}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._lineage_path = self.run_dir / "lineage.jsonl"

    @property
    def lineage_path(self) -> Path:
        return self._lineage_path

    def write_manifest(self, payload: dict[str, Any]) -> None:
        write_json(self.run_dir / "manifest.json", payload)

    def write_candidate(self, candidate: KernelCandidate) -> Path:
        digest_hex = candidate.artifact_digest.removeprefix("sha256:")
        path = self.run_dir / "artifacts" / f"{digest_hex}{candidate.source_suffix}"
        if path.exists():
            if path.read_bytes() != candidate.source_bytes:
                raise RuntimeError(f"content-address collision at {path}")
            return path
        _write_bytes_atomic(path, candidate.source_bytes)
        return path

    def write_report(self, observation: KernelBenchmarkObservation) -> str | None:
        if observation.report is None:
            return None
        content = observation.report.model_dump_json(indent=2)
        digest = content_digest(content)
        path = self.run_dir / "reports" / f"{digest.removeprefix('sha256:')}.json"
        if not path.exists():
            write_text_atomic(path, content)
        return digest

    def append_attempt(self, record: KernelAttemptRecord) -> Path:
        attempt_path = self.run_dir / "attempts" / f"{record.attempt_id}.json"
        if attempt_path.exists():
            raise RuntimeError(f"attempt already exists: {record.attempt_id}")
        write_text_atomic(attempt_path, record.model_dump_json(indent=2))

        line = (json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        append_bytes_locked(self._lineage_path, line)
        return attempt_path

    def write_champion(self, candidate: KernelCandidate, record: KernelAttemptRecord) -> None:
        _write_bytes_atomic(self.run_dir / f"champion{candidate.source_suffix}", candidate.source_bytes)
        write_text_atomic(self.run_dir / "champion.json", record.model_dump_json(indent=2))

    def write_summary(self, result: KernelEvolutionResult) -> None:
        write_text_atomic(self.run_dir / "summary.json", result.model_dump_json(indent=2))
