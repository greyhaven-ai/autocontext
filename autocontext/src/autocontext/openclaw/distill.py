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
import re
import subprocess
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field

from autocontext.offline import require_runtime_available
from autocontext.security.child_process_env import child_process_env_without_control_plane_secrets
from autocontext.security.confined_files import (
    ConfinedPathError,
    atomic_write_confined_text,
    list_confined_regular_files,
    read_confined_text,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings


class DistillJobError(Exception):
    """Raised on invalid distillation job operations."""


DistillJobStatus = Literal["pending", "running", "completed", "failed"]
_DISTILL_JOB_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_DISTILL_JOB_DIRECTORY = "_openclaw_distill_jobs"
_MAX_DISTILL_JOB_BYTES = 1024 * 1024
_MAX_DISTILL_JOBS = 10_000

# Valid state transitions: source → set of allowed targets
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "failed"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


class DistillJob(BaseModel):
    """Full lifecycle model for a distillation job."""

    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[0-9a-f]{32}$")
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
    """Launch an argv-configured sidecar and rely on callbacks for progress."""

    def __init__(self, command_argv: Sequence[str], *, cwd: Path) -> None:
        self._command_argv = _validate_command_argv(command_argv)
        self._cwd = cwd

    def launch(self, job_id: str, scenario: str, config: dict[str, Any]) -> None:
        require_runtime_available("openclaw-cli")
        if "\x00" in job_id or "\x00" in scenario:
            raise DistillJobError("distill job values must not contain NUL bytes")
        if ("{job_id}" in self._command_argv and job_id.lstrip().startswith("-")) or (
            "{scenario}" in self._command_argv and scenario.lstrip().startswith("-")
        ):
            raise DistillJobError("distill job placeholder values must not be option-shaped")
        command = [
            job_id if argument == "{job_id}" else scenario if argument == "{scenario}" else argument
            for argument in self._command_argv
        ]
        env = child_process_env_without_control_plane_secrets()
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


_DISTILL_COMMAND_PLACEHOLDERS = frozenset({"{job_id}", "{scenario}"})


def _validate_command_argv(command_argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command_argv, (str, bytes)) or not command_argv:
        raise DistillJobError("distill sidecar command must be a non-empty JSON argv array")
    validated: list[str] = []
    for index, argument in enumerate(command_argv):
        if not isinstance(argument, str) or not argument or "\x00" in argument:
            raise DistillJobError("distill sidecar argv entries must be non-empty strings without NUL bytes")
        if ("{" in argument or "}" in argument) and argument not in _DISTILL_COMMAND_PLACEHOLDERS:
            raise DistillJobError(
                "distill sidecar placeholders must be whole argv entries and may only be {job_id} or {scenario}",
            )
        if index == 0 and argument in _DISTILL_COMMAND_PLACEHOLDERS:
            raise DistillJobError("distill sidecar executable must be a fixed argv entry, not a placeholder")
        validated.append(argument)
    return tuple(validated)


def _parse_command_argv(raw_command: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(raw_command)
    except json.JSONDecodeError as exc:
        raise DistillJobError(
            "AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_COMMAND must be a JSON argv array; shell command strings are not allowed",
        ) from exc
    if not isinstance(parsed, list):
        raise DistillJobError(
            "AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_COMMAND must be a JSON argv array",
        )
    return _validate_command_argv(parsed)


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
        require_runtime_available("openclaw-factory", settings=settings)
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

    command_spec = settings.openclaw_distill_sidecar_command.strip()
    if command_spec:
        return CommandDistillSidecar(_parse_command_argv(command_spec), cwd=cwd or settings.knowledge_root.parent)
    return None


class DistillJobManager:
    """Manages distillation job persistence and lifecycle transitions."""

    def __init__(self, knowledge_root: Path) -> None:
        self._knowledge_root = knowledge_root
        self._jobs_dir = knowledge_root / _DISTILL_JOB_DIRECTORY

    @staticmethod
    def _validated_job_id(job_id: str) -> str | None:
        if isinstance(job_id, str) and _DISTILL_JOB_ID_PATTERN.fullmatch(job_id) is not None:
            return job_id
        return None

    def _write_job(self, job: DistillJob) -> None:
        safe_job_id = self._validated_job_id(job.job_id)
        if safe_job_id is None:
            raise DistillJobError("invalid distill job id")
        try:
            atomic_write_confined_text(
                self._knowledge_root,
                (_DISTILL_JOB_DIRECTORY,),
                f"{safe_job_id}.json",
                job.model_dump_json(indent=2),
                max_bytes=_MAX_DISTILL_JOB_BYTES,
            )
        except (ConfinedPathError, OSError) as exc:
            raise DistillJobError("distill job store is unavailable") from exc

    def _read_job(self, job_id: str) -> DistillJob | None:
        safe_job_id = self._validated_job_id(job_id)
        if safe_job_id is None:
            return None
        try:
            raw = read_confined_text(
                self._knowledge_root,
                (_DISTILL_JOB_DIRECTORY,),
                f"{safe_job_id}.json",
                max_bytes=_MAX_DISTILL_JOB_BYTES,
            )
            if raw is None:
                return None
            job = DistillJob.model_validate_json(raw)
            return job if job.job_id == safe_job_id else None
        except (ConfinedPathError, FileNotFoundError, OSError, ValueError):
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
        try:
            names = list_confined_regular_files(
                self._knowledge_root,
                (_DISTILL_JOB_DIRECTORY,),
                suffix=".json",
                max_entries=_MAX_DISTILL_JOBS,
            )
        except (ConfinedPathError, FileNotFoundError, OSError):
            return []
        jobs: list[DistillJob] = []
        for name in names:
            job = self._read_job(name.removesuffix(".json"))
            if job is not None and (scenario is None or job.scenario == scenario):
                jobs.append(job)
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
                f"Invalid transition: {job.status} → {target_status} (allowed: {allowed or 'none — terminal state'})"
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
