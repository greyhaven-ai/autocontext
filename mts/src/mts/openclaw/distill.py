"""Distillation job manager for OpenClaw sidecar integration (AC-208).

Provides:
- DistillJob: Pydantic model for full job lifecycle state
- DistillJobManager: persistence and state transitions for distill jobs
- DistillSidecarProtocol: structural typing for sidecar implementations
- DistillJobError: job lifecycle error
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class DistillJobError(Exception):
    """Raised on invalid distillation job operations."""


DistillJobStatus = Literal["pending", "running", "completed", "failed"]

# Valid state transitions: source → set of allowed targets
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


class DistillJob(BaseModel):
    """Full lifecycle model for a distillation job."""

    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    scenario: str
    status: DistillJobStatus = "pending"
    source_artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    result_artifact_id: str | None = None
    error_message: str | None = None
    training_config: dict[str, Any] = Field(default_factory=dict)
    training_metrics: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class DistillSidecarProtocol(Protocol):
    """Structural typing for distillation sidecar implementations."""

    def launch(self, job_id: str, scenario: str, config: dict[str, Any]) -> None:
        """Launch a distillation job on the sidecar."""
        ...

    def poll(self, job_id: str) -> dict[str, Any]:
        """Poll job status from the sidecar."""
        ...


class DistillJobManager:
    """Manages distillation job persistence and lifecycle transitions."""

    def __init__(self, knowledge_root: Path) -> None:
        self._jobs_dir = knowledge_root / "_openclaw_distill_jobs"

    def _ensure_dir(self) -> None:
        self._jobs_dir.mkdir(parents=True, exist_ok=True)

    def _job_path(self, job_id: str) -> Path:
        return self._jobs_dir / f"{job_id}.json"

    def _write_job(self, job: DistillJob) -> None:
        self._ensure_dir()
        self._job_path(job.job_id).write_text(
            job.model_dump_json(indent=2), encoding="utf-8",
        )

    def _read_job(self, job_id: str) -> DistillJob | None:
        path = self._job_path(job_id)
        if not path.exists():
            return None
        try:
            return DistillJob.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def create_job(
        self,
        scenario: str,
        source_artifact_ids: list[str] | None = None,
        training_config: dict[str, Any] | None = None,
    ) -> DistillJob:
        """Create a new pending distillation job."""
        job = DistillJob(
            scenario=scenario,
            source_artifact_ids=source_artifact_ids or [],
            training_config=training_config or {},
        )
        self._write_job(job)
        return job

    def get_job(self, job_id: str) -> DistillJob | None:
        """Fetch a job by ID, or None if not found."""
        return self._read_job(job_id)

    def list_jobs(self, scenario: str | None = None) -> list[DistillJob]:
        """List all jobs, optionally filtered by scenario."""
        if not self._jobs_dir.exists():
            return []
        jobs: list[DistillJob] = []
        for path in sorted(self._jobs_dir.glob("*.json")):
            try:
                job = DistillJob.model_validate_json(path.read_text(encoding="utf-8"))
                if scenario is None or job.scenario == scenario:
                    jobs.append(job)
            except Exception:
                continue
        return jobs

    def transition(
        self,
        job_id: str,
        target_status: DistillJobStatus,
        *,
        result_artifact_id: str | None = None,
        error_message: str | None = None,
        training_metrics: dict[str, Any] | None = None,
    ) -> DistillJob | None:
        """Transition a job to a new status with validation.

        Returns the updated job, or None if job not found.
        Raises DistillJobError on invalid transitions.
        """
        job = self._read_job(job_id)
        if job is None:
            return None

        allowed = _VALID_TRANSITIONS.get(job.status, set())
        if target_status not in allowed:
            raise DistillJobError(
                f"Invalid transition: {job.status} → {target_status} "
                f"(allowed: {allowed or 'none — terminal state'})"
            )

        now = datetime.now(UTC).isoformat()
        job.status = target_status

        if target_status == "running":
            job.started_at = now
        elif target_status in ("completed", "failed"):
            job.completed_at = now

        if result_artifact_id is not None:
            job.result_artifact_id = result_artifact_id
        if error_message is not None:
            job.error_message = error_message
        if training_metrics is not None:
            job.training_metrics = training_metrics

        self._write_job(job)
        return job

    def active_job_count(self) -> int:
        """Count jobs in pending or running state."""
        return sum(1 for j in self.list_jobs() if j.status in ("pending", "running"))
