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
    content_digest,
    kernel_benchmark_report_digest,
)
from autocontext.util.file_lock import append_bytes_locked
from autocontext.util.json_io import write_json, write_text_atomic

_SAFE_ATTEMPT_ID = re.compile(r"attempt_[0-9a-f]{32}")


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

    def __init__(
        self,
        root: Path,
        run_id: str,
        *,
        sealed_audit_root: Path | None = None,
        quarantine_primary_evidence: bool = False,
        resume: bool = False,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) or ".." in run_id:
            raise ValueError("run_id must be a safe path segment")
        self.run_id = run_id
        self.run_dir = root / run_id
        populated = self.run_dir.exists() and any(self.run_dir.iterdir())
        if resume:
            if not populated:
                raise FileNotFoundError(f"kernel run directory does not contain resumable state: {self.run_dir}")
        elif populated:
            raise FileExistsError(f"kernel run directory is not empty: {self.run_dir}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._lineage_path = self.run_dir / "lineage.jsonl"
        self._quarantine_primary_evidence = quarantine_primary_evidence
        self._sealed_audit_dir: Path | None = None
        if sealed_audit_root is not None:
            audit_segment = canonical_digest({"run_id": run_id}).removeprefix("sha256:")
            self._sealed_audit_dir = sealed_audit_root / audit_segment
            if resume:
                if not self._sealed_audit_dir.is_dir():
                    raise FileNotFoundError(
                        f"sealed adaptive evidence is required to resume this run: {self._sealed_audit_dir}"
                    )
            else:
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

    def write_report(self, observation: KernelBenchmarkObservation, *, publish: bool = True) -> str | None:
        if observation.report is None:
            return None
        digest = kernel_benchmark_report_digest(observation.report)
        if not publish:
            return digest
        content = observation.report.model_dump_json(indent=2)
        path = self.run_dir / "reports" / f"{digest.removeprefix('sha256:')}.json"
        if not path.exists():
            write_text_atomic(path, content)
        return digest

    def _public_attempt_payload(self, record: KernelAttemptRecord) -> dict[str, Any]:
        self._validate_attempt_id(record.attempt_id)
        payload = record.model_dump(mode="json")
        should_seal = record.schema_version == "autocontext.kernel-lineage/v4" and (
            self._quarantine_primary_evidence
            or record.confirmation_observation is not None
            or record.confirmation_decision is not None
        )
        if not should_seal:
            return payload
        if self._sealed_audit_dir is None:
            raise RuntimeError("v4 adaptive evidence requires a separate sealed audit root")
        if record.primary_decision is None:
            raise RuntimeError("v4 adaptive evidence requires a primary decision")

        sealed_payload = {
            "schema_version": "autocontext.kernel-adaptive-evidence-audit/v1",
            "run_id": record.run_id,
            "attempt_id": record.attempt_id,
            "decision_policy_id": record.decision_policy_id,
            "report_digest": record.report_digest,
            "confirmation_report_digest": record.confirmation_report_digest,
            # The sealed record is self-contained.  This is the durable source
            # for primary reports, derived statistics, detailed feedback, and
            # every confirmation outcome while adaptive generation is active.
            "attempt": payload,
        }
        audit_digest = canonical_digest(sealed_payload)
        sealed_path = self._sealed_audit_dir / f"{record.attempt_id}.json"
        # Preserve report metadata insertion order: report digests bind the
        # model's JSON representation, while audit_digest above remains the
        # canonical identity for the complete payload.
        sealed_content = json.dumps(sealed_payload, indent=2)
        if sealed_path.exists():
            if sealed_path.read_text(encoding="utf-8") != sealed_content:
                raise RuntimeError(f"sealed confirmation audit changed at {sealed_path}")
        else:
            write_text_atomic(sealed_path, sealed_content)
            if os.name != "nt":
                sealed_path.chmod(0o600)
        return self._public_projection(record, audit_digest)

    @staticmethod
    def _public_projection(record: KernelAttemptRecord, audit_digest: str) -> dict[str, Any]:
        if record.primary_decision is None:
            raise ValueError("v4 adaptive evidence requires a primary decision")
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
            "primary_gates": [
                {"name": gate.name, "status": gate.status} for gate in record.primary_decision.gates
            ],
            "confirmation_gates": (
                [
                    {"name": gate.name, "status": gate.status}
                    for gate in record.confirmation_decision.gates
                ]
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
        self._validate_attempt_id(record.attempt_id)
        attempt_path = self.run_dir / "attempts" / f"{record.attempt_id}.json"
        if attempt_path.exists():
            raise RuntimeError(f"attempt already exists: {record.attempt_id}")
        payload = self._public_attempt_payload(record)
        serialized = json.dumps(payload, indent=2)
        write_text_atomic(attempt_path, serialized)

        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        append_bytes_locked(self._lineage_path, line)
        return attempt_path

    def write_champion(self, candidate: KernelCandidate, record: KernelAttemptRecord) -> None:
        _write_bytes_atomic(self.run_dir / f"champion{candidate.source_suffix}", candidate.source_bytes)
        payload = self._public_attempt_payload(record)
        write_text_atomic(self.run_dir / "champion.json", json.dumps(payload, indent=2))

    def write_summary(self, result: KernelEvolutionResult) -> None:
        write_text_atomic(self.run_dir / "summary.json", result.model_dump_json(indent=2))

    def read_manifest(self) -> dict[str, Any]:
        path = self.run_dir / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("run_id") != self.run_id:
            raise ValueError("kernel run manifest identity is invalid")
        return payload

    def read_attempts(self) -> list[KernelAttemptRecord]:
        """Load full attempt evidence and verify public lineage/audit bindings."""
        if not self._lineage_path.is_file():
            return []
        lines = self._lineage_path.read_text(encoding="utf-8").splitlines()
        attempts: list[KernelAttemptRecord] = []
        for line_number, line in enumerate(lines, start=1):
            try:
                public = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"kernel lineage line {line_number} is invalid JSON") from exc
            if not isinstance(public, dict):
                raise ValueError(f"kernel lineage line {line_number} must be an object")
            attempt_id = public.get("attempt_id")
            if not isinstance(attempt_id, str):
                raise ValueError(f"kernel lineage line {line_number} has no attempt identity")
            self._validate_attempt_id(attempt_id)
            attempt_path = self.run_dir / "attempts" / f"{attempt_id}.json"
            if not attempt_path.is_file():
                raise ValueError(f"kernel lineage attempt file is missing: {attempt_id}")
            persisted_public = json.loads(attempt_path.read_text(encoding="utf-8"))
            if persisted_public != public:
                raise ValueError(f"kernel lineage and attempt file disagree: {attempt_id}")
            attempt = self._validated_attempt(public, attempt_id)
            self._validate_attempt_artifacts(attempt)
            attempts.append(attempt)
        generations = [attempt.generation for attempt in attempts]
        if generations != list(range(len(attempts))):
            raise ValueError("kernel lineage generations must be contiguous from zero")
        return attempts

    def reconcile_attempt_files(self, *, expected_attempt_ids: set[str]) -> None:
        """Append fully persisted deterministic attempts missing only their lineage line."""
        self.read_attempts()
        lines = self._lineage_path.read_text(encoding="utf-8").splitlines() if self._lineage_path.is_file() else []
        lineage_ids: set[str] = set()
        for line_number, line in enumerate(lines, start=1):
            try:
                public = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"kernel lineage line {line_number} is invalid JSON") from exc
            attempt_id = public.get("attempt_id") if isinstance(public, dict) else None
            if not isinstance(attempt_id, str) or attempt_id in lineage_ids:
                raise ValueError("kernel lineage contains an invalid or duplicate attempt identity")
            self._validate_attempt_id(attempt_id)
            lineage_ids.add(attempt_id)

        orphans: list[tuple[int, dict[str, Any]]] = []
        for path in sorted((self.run_dir / "attempts").glob("*.json")):
            if path.stem in lineage_ids:
                continue
            public = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(public, dict) or public.get("attempt_id") != path.stem:
                raise ValueError(f"orphan kernel attempt identity is invalid: {path.name}")
            if path.stem not in expected_attempt_ids:
                raise ValueError(f"orphan kernel attempt has no durable evaluation claim: {path.name}")
            attempt = self._validated_attempt(public, path.stem)
            self._validate_attempt_artifacts(attempt)
            orphans.append((attempt.generation, public))

        orphans.sort(key=lambda item: item[0])
        expected_generations = list(range(len(lines), len(lines) + len(orphans)))
        if [generation for generation, _public in orphans] != expected_generations:
            raise ValueError("orphan kernel attempts do not form the exact next lineage suffix")
        for _generation, public in orphans:
            encoded_line = (json.dumps(public, separators=(",", ":")) + "\n").encode("utf-8")
            append_bytes_locked(self._lineage_path, encoded_line)

    def _validated_attempt(self, public: dict[str, Any], attempt_id: str) -> KernelAttemptRecord:
        self._validate_attempt_id(attempt_id)
        payload: Any = public
        expected_digest: str | None = None
        if public.get("schema_version") == "autocontext.kernel-lineage-public/v4":
            sealed = self._read_sealed_attempt(attempt_id)
            expected_digest = public.get("confirmation_audit_digest")
            if not isinstance(expected_digest, str) or canonical_digest(sealed) != expected_digest:
                raise ValueError(f"sealed adaptive evidence digest changed: {attempt_id}")
            payload = sealed.get("attempt")
            if not isinstance(payload, dict):
                raise ValueError(f"sealed adaptive evidence is incomplete: {attempt_id}")
        attempt = KernelAttemptRecord.model_validate(payload)
        if attempt.run_id != self.run_id or attempt.attempt_id != attempt_id:
            raise ValueError(f"kernel attempt identity changed: {attempt_id}")
        expected_public = (
            self._public_projection(attempt, expected_digest)
            if expected_digest is not None
            else attempt.model_dump(mode="json")
        )
        if public != expected_public:
            raise ValueError(f"public kernel attempt projection changed: {attempt_id}")
        return attempt

    def _validate_attempt_artifacts(self, attempt: KernelAttemptRecord) -> None:
        self.read_candidate(attempt)
        if attempt.report_digest is None:
            return
        report_path = self.run_dir / "reports" / (
            f"{attempt.report_digest.removeprefix('sha256:')}.json"
        )
        if report_path.is_file():
            if content_digest(report_path.read_bytes()) != attempt.report_digest:
                raise ValueError(f"kernel report artifact changed: {attempt.report_digest}")
        elif not self._quarantine_primary_evidence:
            raise ValueError(f"kernel report artifact is missing: {attempt.report_digest}")

    def read_candidate(self, record: KernelAttemptRecord) -> KernelCandidate:
        self._validate_attempt_id(record.attempt_id)
        path = self.run_dir / "artifacts" / f"{record.artifact_digest.removeprefix('sha256:')}{record.source_suffix}"
        if not path.is_file():
            raise ValueError(f"kernel candidate artifact is missing: {record.artifact_digest}")
        candidate = KernelCandidate(
            source=path.read_bytes().decode("utf-8"),
            source_suffix=record.source_suffix,
            entrypoint=record.entrypoint,
        )
        if candidate.artifact_digest != record.artifact_digest or candidate.source_digest != record.source_digest:
            raise ValueError(f"kernel candidate artifact changed: {record.artifact_digest}")
        return candidate

    def validate_champion(self, candidate: KernelCandidate, record: KernelAttemptRecord) -> None:
        """Verify the mutable named champion agrees with its sealed lineage record."""
        expected_source = self.run_dir / f"champion{candidate.source_suffix}"
        named_sources = {
            path for path in self.run_dir.glob("champion*") if path.name != "champion.json"
        }
        if named_sources != {expected_source} or expected_source.read_bytes() != candidate.source_bytes:
            raise ValueError("named champion source artifact is missing, changed, or ambiguous")
        champion_path = self.run_dir / "champion.json"
        if not champion_path.is_file():
            raise ValueError("named champion record is missing")
        public = json.loads(champion_path.read_text(encoding="utf-8"))
        if not isinstance(public, dict) or public.get("attempt_id") != record.attempt_id:
            raise ValueError("named champion record identity is invalid")
        persisted = json.loads(
            (self.run_dir / "attempts" / f"{record.attempt_id}.json").read_text(encoding="utf-8")
        )
        if public != persisted:
            raise ValueError("named champion record disagrees with its append-only attempt")

    def read_summary(self) -> KernelEvolutionResult | None:
        path = self.run_dir / "summary.json"
        if not path.is_file():
            return None
        return KernelEvolutionResult.model_validate_json(path.read_text(encoding="utf-8"))

    def _read_sealed_attempt(self, attempt_id: str) -> dict[str, Any]:
        self._validate_attempt_id(attempt_id)
        candidates = []
        if self._sealed_audit_dir is not None:
            candidates.append(self._sealed_audit_dir / f"{attempt_id}.json")
        candidates.append(self.run_dir / "audit" / "confirmation" / f"{attempt_id}.json")
        for path in candidates:
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
        raise ValueError(f"sealed adaptive evidence is unavailable: {attempt_id}")

    @staticmethod
    def _validate_attempt_id(attempt_id: str) -> None:
        if not _SAFE_ATTEMPT_ID.fullmatch(attempt_id):
            raise ValueError("kernel attempt id must be a safe deterministic path segment")

    def release_sealed_audit(self) -> Path | None:
        """Publish confirmation audit material only after adaptive generation ends."""

        if self._sealed_audit_dir is None:
            return None
        destination = self.run_dir / "audit" / "confirmation"
        destination.mkdir(parents=True, exist_ok=True)
        sources = sorted(self._sealed_audit_dir.glob("*.json"))
        expected_names = {source.name for source in sources}
        observed_names = {path.name for path in destination.glob("*.json")}
        if not observed_names <= expected_names:
            raise RuntimeError("released confirmation audit contains an unknown record")
        for source in sources:
            target = destination / source.name
            if target.exists():
                if target.read_bytes() != source.read_bytes():
                    raise RuntimeError(f"released confirmation audit changed at {target}")
            else:
                shutil.copyfile(source, target)
            if os.name != "nt":
                target.chmod(0o600)
        write_json(
            destination.parent / "release.json",
            {
                "schema_version": "autocontext.kernel-audit-release/v1",
                "status": "terminal",
                "adaptive_evidence_records": len(sources),
            },
        )
        return destination
