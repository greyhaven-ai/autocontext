"""Distillation job manager for OpenClaw sidecar integration (AC-208).

Provides:
- DistillJob: Pydantic model for full job lifecycle state
- DistillJobManager: persistence and state transitions for distill jobs
- DistillSidecarProtocol: structural typing for sidecar implementations
- DistillJobError: job lifecycle error
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import shlex
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings


class DistillJobError(Exception):
    """Raised on invalid distillation job operations."""


DistillJobStatus = Literal["pending", "running", "completed", "failed"]

# Valid state transitions: source → set of allowed targets
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "failed"},
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


class CommandDistillSidecar:
    """Launches an external sidecar command and relies on API callbacks for progress."""

    def __init__(self, command_template: str, *, cwd: Path) -> None:
        self._command_template = command_template
        self._cwd = cwd

    def launch(self, job_id: str, scenario: str, config: dict[str, Any]) -> None:
        command = shlex.split(
            self._command_template.format(job_id=job_id, scenario=scenario),
        )
        env = os.environ.copy()
        env["AUTOCONTEXT_DISTILL_JOB_ID"] = job_id
        env["AUTOCONTEXT_DISTILL_SCENARIO"] = scenario
        env["AUTOCONTEXT_DISTILL_TRAINING_CONFIG"] = json.dumps(config, sort_keys=True)
        subprocess.Popen(  # noqa: S603
            command,
            cwd=self._cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def poll(self, job_id: str) -> dict[str, Any]:
        del job_id
        return {}


def _load_factory(factory_path: str) -> Callable[..., object]:
    module_name, sep, attr_name = factory_path.partition(":")
    if not sep or not module_name or not attr_name:
        raise DistillJobError(
            "AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_FACTORY must be in the form 'module:callable'",
        )
    module = importlib.import_module(module_name)
    try:
        factory = getattr(module, attr_name)
    except AttributeError as exc:
        raise DistillJobError(f"Distill sidecar factory {factory_path!r} not found") from exc
    if not callable(factory):
        raise DistillJobError(f"Distill sidecar factory {factory_path!r} is not callable")
    return cast(Callable[..., object], factory)


def load_distill_sidecar(settings: AppSettings, *, cwd: Path | None = None) -> DistillSidecarProtocol | None:
    """Resolve the configured distillation sidecar, if any."""
    factory_path = settings.openclaw_distill_sidecar_factory.strip()
    if factory_path:
        factory = _load_factory(factory_path)
        signature = inspect.signature(factory)
        if len(signature.parameters) == 0:
            sidecar = factory()
        else:
            sidecar = factory(settings)
        if not isinstance(sidecar, DistillSidecarProtocol):
            raise DistillJobError(
                f"Distill sidecar factory {factory_path!r} did not return a DistillSidecarProtocol implementation",
            )
        return sidecar

    command_template = settings.openclaw_distill_sidecar_command.strip()
    if command_template:
        return CommandDistillSidecar(command_template, cwd=cwd or settings.knowledge_root.parent)
    return None


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
            logger.debug("openclaw.distill: caught Exception", exc_info=True)
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
                logger.debug("openclaw.distill: caught Exception", exc_info=True)
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
        if target_status == "completed" and not (result_artifact_id or job.result_artifact_id):
            raise DistillJobError("Completed distill jobs require a result_artifact_id")
        if target_status == "failed" and not (error_message or job.error_message):
            raise DistillJobError("Failed distill jobs require an error_message")

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
