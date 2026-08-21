"""Crash-resilient, content-addressed artifacts for a kernel search lineage."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from autocontext.kernel_evolution.models import (
    KernelAttemptRecord,
    KernelBenchmarkObservation,
    KernelCandidate,
    KernelEvolutionResult,
    canonical_digest,
    kernel_benchmark_report_digest,
)
from autocontext.util.file_lock import append_bytes_locked
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

    def __init__(self, root: Path, run_id: str, *, sealed_audit_root: Path | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) or ".." in run_id:
            raise ValueError("run_id must be a safe path segment")
        self.run_id = run_id
        self.run_dir = root / run_id
        if self.run_dir.exists() and any(self.run_dir.iterdir()):
            raise FileExistsError(f"kernel run directory is not empty; resume is not supported: {self.run_dir}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._lineage_path = self.run_dir / "lineage.jsonl"
        self._sealed_audit_dir: Path | None = None
        if sealed_audit_root is not None:
            audit_segment = canonical_digest({"run_id": run_id}).removeprefix("sha256:")
            self._sealed_audit_dir = sealed_audit_root / audit_segment
            self._sealed_audit_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            if os.name != "nt":
                self._sealed_audit_dir.chmod(0o700)

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
        digest = kernel_benchmark_report_digest(observation.report)
        path = self.run_dir / "reports" / f"{digest.removeprefix('sha256:')}.json"
        if not path.exists():
            write_text_atomic(path, content)
        return digest

    def _public_attempt_payload(self, record: KernelAttemptRecord) -> dict[str, Any]:
        payload = record.model_dump(mode="json")
        if record.schema_version != "autocontext.kernel-lineage/v4" or record.confirmation_observation is None:
            return payload
        if self._sealed_audit_dir is None:
            raise RuntimeError("v4 confirmation evidence requires a separate sealed audit root")
        if record.primary_decision is None:
            raise RuntimeError("v4 confirmation evidence requires a primary decision")

        sealed_payload = {
            "schema_version": "autocontext.kernel-confirmation-audit/v1",
            "run_id": record.run_id,
            "attempt_id": record.attempt_id,
            "decision_policy_id": record.decision_policy_id,
            "confirmation_report_digest": record.confirmation_report_digest,
            "confirmation_observation": record.confirmation_observation.model_dump(mode="json"),
            "confirmation_decision": (
                record.confirmation_decision.model_dump(mode="json")
                if record.confirmation_decision is not None
                else None
            ),
        }
        audit_digest = canonical_digest(sealed_payload)
        sealed_path = self._sealed_audit_dir / f"{record.attempt_id}.json"
        sealed_content = json.dumps(sealed_payload, indent=2, sort_keys=True)
        if sealed_path.exists():
            if sealed_path.read_text(encoding="utf-8") != sealed_content:
                raise RuntimeError(f"sealed confirmation audit changed at {sealed_path}")
        else:
            write_text_atomic(sealed_path, sealed_content)
            if os.name != "nt":
                sealed_path.chmod(0o600)
        return {
            "schema_version": "autocontext.kernel-lineage-public/v4",
            "run_id": record.run_id,
            "attempt_id": record.attempt_id,
            "generation": record.generation,
            "role": record.role,
            "artifact_digest": record.artifact_digest,
            "parent_attempt_id": record.parent_attempt_id,
            "parent_artifact_digest": record.parent_artifact_digest,
            "decision": record.decision,
            "reason": record.reason,
            "decision_policy_id": record.decision_policy_id,
            "report_digest": record.report_digest,
            "confirmation_audit_digest": audit_digest,
            "confirmation_report_digest": record.confirmation_report_digest,
            "primary_gates": [gate.model_dump(mode="json") for gate in record.primary_decision.gates],
            "confirmation_gates": (
                [gate.model_dump(mode="json") for gate in record.confirmation_decision.gates]
                if record.confirmation_decision is not None
                else []
            ),
            "sequential_evidence": (
                record.sequential_evidence.model_dump(mode="json")
                if record.sequential_evidence is not None
                else None
            ),
        }

    def append_attempt(self, record: KernelAttemptRecord) -> Path:
        attempt_path = self.run_dir / "attempts" / f"{record.attempt_id}.json"
        if attempt_path.exists():
            raise RuntimeError(f"attempt already exists: {record.attempt_id}")
        payload = self._public_attempt_payload(record)
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        write_text_atomic(attempt_path, serialized)

        line = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        append_bytes_locked(self._lineage_path, line)
        return attempt_path

    def write_champion(self, candidate: KernelCandidate, record: KernelAttemptRecord) -> None:
        _write_bytes_atomic(self.run_dir / f"champion{candidate.source_suffix}", candidate.source_bytes)
        payload = self._public_attempt_payload(record)
        write_text_atomic(self.run_dir / "champion.json", json.dumps(payload, indent=2, sort_keys=True))

    def write_summary(self, result: KernelEvolutionResult) -> None:
        write_text_atomic(self.run_dir / "summary.json", result.model_dump_json(indent=2))

    def release_sealed_audit(self) -> Path | None:
        """Publish confirmation audit material only after adaptive generation ends."""

        if self._sealed_audit_dir is None:
            return None
        destination = self.run_dir / "audit" / "confirmation"
        destination.mkdir(parents=True, exist_ok=False)
        for source in sorted(self._sealed_audit_dir.glob("*.json")):
            target = destination / source.name
            shutil.copyfile(source, target)
            if os.name != "nt":
                target.chmod(0o600)
        write_json(
            destination.parent / "release.json",
            {
                "schema_version": "autocontext.kernel-audit-release/v1",
                "status": "terminal",
                "confirmation_records": len(tuple(destination.glob("*.json"))),
            },
        )
        return destination
