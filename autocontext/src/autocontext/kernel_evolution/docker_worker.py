"""OS-isolated Docker GPU benchmark worker (AC-991)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from autocontext.execution.docker_isolation import (
    DockerIsolationLimits,
    build_docker_isolation_command,
    sanitized_docker_environment,
)
from autocontext.execution.scenario_remote_package import require_pinned_runtime_image
from autocontext.kernel_evolution import _process_control
from autocontext.kernel_evolution.benchmark import (
    KernelBenchmarkExecution,
    _fingerprint_paths,
    _reject_json_constant,
)
from autocontext.kernel_evolution.docker_supervisor import (
    SUPERVISOR_PROTOCOL_VERSION,
    DockerSupervisorCompletion,
)
from autocontext.kernel_evolution.docker_watchdog import (
    CLEANUP_TIMEOUT_SECONDS,
    DOCKER_KERNEL_OWNER_LABEL,
    docker_container_missing,
)
from autocontext.kernel_evolution.docker_worker_runtime import (
    copy_live_tmpfs_report,
    execute_supervised_container,
)
from autocontext.kernel_evolution.gpu_attestation import (
    DockerGPUDeviceAttestation,
    DockerGPUDeviceAttestor,
    DockerGPUDeviceGrant,
    NvidiaSMIGPUDeviceAttestor,
)
from autocontext.kernel_evolution.models import ARTIFACT_IDENTITY_VERSION, KernelCandidate, content_digest

_OWNER_LABEL = DOCKER_KERNEL_OWNER_LABEL
_EXPIRY_LABEL = "ai.autocontext.expires-at"
_CLEANUP_TIMEOUT_SECONDS = CLEANUP_TIMEOUT_SECONDS
_REPORT_EXTRACTION_GRACE_SECONDS = 10.0
_SUPERVISOR_CONTAINER_PATH = "/autocontext-docker-supervisor.py"


def _validate_json_nesting(payload: bytes, limits: _process_control.ReportLimits) -> str:
    """Reject excessive hostile JSON nesting before invoking the decoder."""
    text = payload.decode("utf-8")
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > limits.max_depth:
                raise ValueError(f"benchmark report JSON exceeded max_report_depth={limits.max_depth}")
        elif character in "]}":
            depth -= 1
    return text


def _validate_json_entries(decoded: object, limits: _process_control.ReportLimits) -> None:
    """Bound the decoded report's total mapping members and list elements."""
    entries = 0
    pending: list[object] = [decoded]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            entries += len(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            entries += len(value)
            pending.extend(value)
        if entries > limits.max_entries:
            raise ValueError(f"benchmark report JSON exceeded max_report_entries={limits.max_entries}")


@dataclass(frozen=True, slots=True)
class DockerKernelWorkerLimits:
    memory_mb: int = 16_384
    cpu_count: float = 8.0
    cpu_time_seconds: int = 600
    pids_limit: int = 128
    max_output_bytes: int = 64_000
    max_report_bytes: int = 2_000_000
    max_report_entries: int = 1_024
    max_report_depth: int = 16
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_workspace_inodes: int = 8_192
    max_gpu_memory_bytes: int = 16 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        DockerIsolationLimits(
            memory_mb=self.memory_mb,
            cpu_count=self.cpu_count,
            cpu_time_seconds=self.cpu_time_seconds,
            pids_limit=self.pids_limit,
        )
        for name in (
            "max_output_bytes",
            "max_report_bytes",
            "max_report_entries",
            "max_report_depth",
            "max_workspace_bytes",
            "max_workspace_inodes",
            "max_gpu_memory_bytes",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")


class DockerKernelBenchmarkRunner:
    """Run a generated kernel behind a locked-down, ephemeral GPU container."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        image: str,
        immutable_paths: Sequence[Path],
        gpu_grant: DockerGPUDeviceGrant,
        gpu_attestor: DockerGPUDeviceAttestor,
        limits: DockerKernelWorkerLimits | None = None,
        docker_binary: str = "docker",
        source_suffix: str = ".py",
        temporary_root: Path | None = None,
    ) -> None:
        if not command or not immutable_paths:
            raise ValueError("Docker kernel worker requires a command and immutable harness paths")
        if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", source_suffix) is None:
            raise ValueError("source_suffix must be a short safe extension")
        rendered = "\0".join(command)
        missing = [item for item in ("{candidate}", "{incumbent}", "{report}") if item not in rendered]
        if missing:
            raise ValueError(f"Docker kernel command is missing placeholders: {', '.join(missing)}")
        require_pinned_runtime_image(image)
        resolved_binary = shutil.which(docker_binary)
        if resolved_binary is None:
            raise RuntimeError(f"Docker executable is unavailable: {docker_binary}")
        self._command = tuple(command)
        self.image = image
        self.docker_binary = resolved_binary
        self._immutable_paths = tuple(Path(path).resolve(strict=True) for path in immutable_paths)
        self._harness_digest = _fingerprint_paths(self._immutable_paths)
        self._supervisor_path = Path(__file__).with_name("docker_supervisor.py").resolve(strict=True)
        self._supervisor_digest = content_digest(self._supervisor_path.read_bytes())
        self.gpu_grant = gpu_grant
        if not gpu_attestor.attestor_id.strip():
            raise ValueError("Docker GPU attestor must expose a non-empty attestor_id")
        self.gpu_attestor = gpu_attestor
        self.limits = limits or DockerKernelWorkerLimits()
        self._source_suffix = source_suffix
        self._temporary_root = temporary_root
        self._report_limits = _process_control.ReportLimits(
            max_bytes=self.limits.max_report_bytes,
            max_entries=self.limits.max_report_entries,
            max_depth=self.limits.max_report_depth,
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "kind": "docker-gpu-isolated",
            "artifact_identity_version": ARTIFACT_IDENTITY_VERSION,
            "image": self.image,
            "docker_binary": self.docker_binary,
            "command": list(self._command),
            "immutable_paths": [str(path) for path in self._immutable_paths],
            "immutable_harness_digest": self._harness_digest,
            "supervisor": {
                "protocol": SUPERVISOR_PROTOCOL_VERSION,
                "source_path": str(self._supervisor_path),
                "source_digest": self._supervisor_digest,
                "report_extraction_grace_seconds": _REPORT_EXTRACTION_GRACE_SECONDS,
                "report_transport": "authenticated-bounded-docker-exec-v1",
            },
            "requested_gpu_grant": asdict(self.gpu_grant),
            "gpu_attestor": self.gpu_attestor.manifest(),
            "limits": asdict(self.limits),
            "network": "deny",
            "ambient_credentials": "scrubbed",
        }

    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if candidate.source_suffix != self._source_suffix or incumbent.source_suffix != self._source_suffix:
            return KernelBenchmarkExecution(returncode=None, error="candidate/incumbent suffix does not match worker")
        attestation, policy_error = self._attest_gpu()
        if policy_error is not None:
            return KernelBenchmarkExecution(
                returncode=None,
                error=policy_error,
                outcome="resource_policy_unsupported",
            )
        assert attestation is not None
        try:
            if (
                _fingerprint_paths(self._immutable_paths) != self._harness_digest
                or content_digest(self._supervisor_path.read_bytes()) != self._supervisor_digest
            ):
                return KernelBenchmarkExecution(
                    returncode=None,
                    error="immutable benchmark harness or Docker supervisor changed before container launch",
                    harness_unchanged=False,
                )
        except (OSError, ValueError) as exc:
            return KernelBenchmarkExecution(
                returncode=None,
                error=f"immutable benchmark harness failed preflight: {exc}",
                harness_unchanged=False,
            )
        try:
            self.reconcile()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return KernelBenchmarkExecution(
                returncode=None,
                error=f"Docker GPU orphan reconciliation failed: {type(exc).__name__}: {exc}",
                outcome="teardown_failed",
            )
        if not self._image_available():
            return KernelBenchmarkExecution(returncode=None, error="pinned Docker GPU image is unavailable")

        with tempfile.TemporaryDirectory(prefix="autoctx-kernel-gpu-", dir=self._temporary_root) as temp_name:
            root = Path(temp_name)
            input_root = root / "input"
            report_root = root / "report"
            input_root.mkdir()
            report_root.mkdir()
            report_root_identity = _process_control.filesystem_object_identity(report_root.lstat())
            candidate_path = input_root / f"candidate{self._source_suffix}"
            incumbent_path = input_root / f"incumbent{self._source_suffix}"
            candidate_path.write_bytes(candidate.source_bytes)
            incumbent_path.write_bytes(incumbent.source_bytes)
            candidate_path.chmod(0o444)
            incumbent_path.chmod(0o444)
            report_path = report_root / "report.json"
            container_name = f"autoctx-kernel-{uuid.uuid4().hex[:20]}"
            execution_expires_at = time.time() + timeout_seconds
            hard_expires_at = execution_expires_at + _REPORT_EXTRACTION_GRACE_SECONDS
            command = self._docker_command(
                container_name,
                input_root,
                candidate,
                incumbent,
                attestation,
                execution_expires_at,
                hard_expires_at,
            )
            execution = self._execute_container(
                command,
                container_name=container_name,
                report_path=report_path,
                report_root_identity=report_root_identity,
                timeout_seconds=timeout_seconds,
                execution_expires_at=execution_expires_at,
                hard_expires_at=hard_expires_at,
            )
            payload = execution.report_payload
            if payload is not None and execution.outcome == "complete" and payload.get("failure_kind") == "oom":
                execution = KernelBenchmarkExecution(
                    **{
                        **asdict(execution),
                        "outcome": "oom",
                        "error": "Benchmark adapter reported a CUDA out-of-memory failure",
                    }
                )
            elif payload is not None and execution.outcome == "complete":
                outcome, detail = self._resource_telemetry_outcome(payload, candidate, incumbent, attestation)
                if outcome is not None:
                    execution = KernelBenchmarkExecution(
                        **{
                            **asdict(execution),
                            "outcome": outcome,
                            "error": detail,
                        }
                    )
            try:
                harness_unchanged = (
                    _fingerprint_paths(self._immutable_paths) == self._harness_digest
                    and content_digest(self._supervisor_path.read_bytes()) == self._supervisor_digest
                )
            except (OSError, ValueError):
                harness_unchanged = False
            candidate_unchanged = self._source_unchanged(candidate_path, candidate)
            incumbent_unchanged = self._source_unchanged(incumbent_path, incumbent)
            return KernelBenchmarkExecution(
                **{
                    **asdict(execution),
                    "harness_unchanged": harness_unchanged,
                    "candidate_unchanged": candidate_unchanged,
                    "incumbent_unchanged": incumbent_unchanged,
                }
            )

    def _attest_gpu(self) -> tuple[DockerGPUDeviceAttestation | None, str | None]:
        if self.gpu_grant.isolation_kind == "visibility-only":
            return None, "GPU memory enforcement is unavailable; use a verified MIG or hardware partition grant"
        try:
            attestation = self.gpu_attestor.attest(self.gpu_grant)
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            return None, f"trusted GPU grant attestation failed: {type(exc).__name__}: {exc}"
        if attestation.attestor_id != self.gpu_attestor.attestor_id:
            return None, "trusted GPU grant attestation returned an unexpected attestor identity"
        if (
            attestation.device_id != self.gpu_grant.device_id
            or attestation.isolation_kind != self.gpu_grant.isolation_kind
        ):
            return None, "trusted GPU grant attestation does not match the requested partition identity"
        expected_capacity = self.gpu_grant.enforced_memory_bytes
        if expected_capacity is None or attestation.enforced_memory_bytes != expected_capacity:
            return None, "trusted GPU grant capacity does not match the configured hard partition capacity"
        if attestation.enforced_memory_bytes > self.limits.max_gpu_memory_bytes:
            return (
                None,
                "GPU partition capacity "
                f"{attestation.enforced_memory_bytes} exceeds configured hard limit "
                f"{self.limits.max_gpu_memory_bytes} bytes",
            )
        return attestation, None

    def reconcile(self, *, now: float | None = None) -> int:
        """Remove and verify expired containers left by crashed coordinators."""

        listed = subprocess.run(  # noqa: S603
            [self.docker_binary, "ps", "-aq", "--filter", f"label={_OWNER_LABEL}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            env=sanitized_docker_environment(),
        )
        container_ids = tuple(item for item in listed.stdout.splitlines() if item.strip())
        if not container_ids:
            return 0
        deadline = time.time() if now is None else now
        expired: list[str] = []
        for listed_id in container_ids:
            inspected = subprocess.run(  # noqa: S603
                [
                    self.docker_binary,
                    "inspect",
                    "--format",
                    f'{{{{.Id}}}}\t{{{{ index .Config.Labels "{_EXPIRY_LABEL}" }}}}',
                    listed_id,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=_CLEANUP_TIMEOUT_SECONDS,
                env=sanitized_docker_environment(),
            )
            if inspected.returncode != 0:
                if docker_container_missing(inspected):
                    continue
                raise RuntimeError((inspected.stderr or inspected.stdout).strip()[-240:])
            lines = inspected.stdout.splitlines()
            if len(lines) != 1:
                raise RuntimeError("Docker GPU orphan inspection returned ambiguous expiry metadata")
            line = lines[0]
            container_id, separator, raw_expiry = line.partition("\t")
            if not separator:
                raise RuntimeError("Docker GPU orphan inspection returned malformed expiry metadata")
            try:
                expires_at = float(raw_expiry)
            except ValueError as exc:
                raise RuntimeError("owned Docker GPU worker has missing or invalid expiry metadata") from exc
            if not math.isfinite(expires_at) or expires_at <= 0:
                raise RuntimeError("owned Docker GPU worker has non-finite or non-positive expiry metadata")
            if expires_at <= deadline:
                expired.append(container_id)
        for container_id in expired:
            self._remove_container(container_id)
        verify = subprocess.run(  # noqa: S603
            [self.docker_binary, "ps", "-aq", "--filter", f"label={_OWNER_LABEL}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            env=sanitized_docker_environment(),
        )
        remaining = {item for item in verify.stdout.splitlines() if item.strip()}
        if any(
            remaining_id.startswith(expired_id) or expired_id.startswith(remaining_id)
            for remaining_id in remaining
            for expired_id in expired
        ):
            raise RuntimeError("expired Docker GPU workers remained after reconciliation")
        return len(expired)

    def _docker_command(
        self,
        container_name: str,
        input_root: Path,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        attestation: DockerGPUDeviceAttestation,
        execution_expires_at: float,
        hard_expires_at: float | None = None,
    ) -> list[str]:
        if hard_expires_at is None:
            hard_expires_at = execution_expires_at + _REPORT_EXTRACTION_GRACE_SECONDS
        replacements = {
            "{candidate}": f"/input/{candidate_path_name(candidate, self._source_suffix)}",
            "{incumbent}": f"/input/{candidate_path_name(incumbent, self._source_suffix, incumbent=True)}",
            "{report}": "/output/report.json",
            "{artifact_identity_version}": candidate.artifact_identity_version,
            "{candidate_artifact_digest}": candidate.artifact_digest,
            "{incumbent_artifact_digest}": incumbent.artifact_digest,
            "{candidate_source_digest}": candidate.source_digest,
            "{incumbent_source_digest}": incumbent.source_digest,
            "{candidate_source_suffix}": candidate.source_suffix,
            "{incumbent_source_suffix}": incumbent.source_suffix,
            "{candidate_entrypoint}": candidate.entrypoint,
            "{incumbent_entrypoint}": incumbent.entrypoint,
        }
        for index in range(len(self._immutable_paths)):
            replacements[f"{{immutable_{index}}}"] = f"/benchmark/{index}"
        argv: list[str] = []
        for raw in self._command:
            value = raw
            for placeholder, replacement in replacements.items():
                value = value.replace(placeholder, replacement)
            argv.append(value)
        supervisor_argv = [
            argv[0],
            "-I",
            "-B",
            "-S",
            _SUPERVISOR_CONTAINER_PATH,
            "--supervise",
            "--report",
            "/output/report.json",
            "--max-report-bytes",
            str(self.limits.max_report_bytes),
            "--execution-deadline-ns",
            str(int(execution_expires_at * 1_000_000_000)),
            "--hard-deadline-ns",
            str(int(hard_expires_at * 1_000_000_000)),
            "--",
            *argv,
        ]
        readonly_mounts = {input_root: "/input", self._supervisor_path: _SUPERVISOR_CONTAINER_PATH}
        readonly_mounts.update({path: f"/benchmark/{index}" for index, path in enumerate(self._immutable_paths)})
        return build_docker_isolation_command(
            docker_binary=self.docker_binary,
            image=self.image,
            container_name=container_name,
            labels={_OWNER_LABEL: container_name, _EXPIRY_LABEL: f"{hard_expires_at:.6f}"},
            limits=DockerIsolationLimits(
                memory_mb=self.limits.memory_mb,
                cpu_count=self.limits.cpu_count,
                cpu_time_seconds=self.limits.cpu_time_seconds,
                pids_limit=self.limits.pids_limit,
            ),
            readonly_mounts=readonly_mounts,
            writable_mounts={},
            tmpfs_mounts={
                "/output": (
                    "rw,noexec,nosuid,nodev,"
                    f"size={self.limits.max_report_bytes},nr_inodes=16,mode=0700,uid={os.getuid()},gid={os.getgid()}"
                ),
                "/tmp": "rw,noexec,nosuid,nodev,size=64m,nr_inodes=1024",
                "/workspace": (
                    f"rw,nosuid,nodev,exec,size={self.limits.max_workspace_bytes},nr_inodes={self.limits.max_workspace_inodes}"
                ),
            },
            argv=supervisor_argv,
            gpu_device=attestation.device_id,
            auto_remove=False,
            interactive=True,
            working_dir="/workspace",
            environment={
                "AUTOCONTEXT_GPU_DEVICE_ID": attestation.device_id,
                "AUTOCONTEXT_GPU_ISOLATION_KIND": attestation.isolation_kind,
                "AUTOCONTEXT_GPU_ENFORCED_MEMORY_BYTES": str(attestation.enforced_memory_bytes),
                "AUTOCONTEXT_GPU_ATTESTOR_ID": attestation.attestor_id,
                "AUTOCONTEXT_GPU_ATTESTATION_DIGEST": attestation.digest,
            },
        )

    def _execute_container(
        self,
        command: list[str],
        *,
        container_name: str,
        report_path: Path,
        report_root_identity: _process_control.FilesystemObjectIdentity,
        timeout_seconds: float,
        execution_expires_at: float,
        hard_expires_at: float,
    ) -> KernelBenchmarkExecution:
        return execute_supervised_container(
            self,
            command,
            container_name=container_name,
            report_path=report_path,
            report_root_identity=report_root_identity,
            timeout_seconds=timeout_seconds,
            execution_expires_at=execution_expires_at,
            hard_expires_at=hard_expires_at,
            max_output_bytes=self.limits.max_output_bytes,
            max_report_bytes=self.limits.max_report_bytes,
        )

    def _create_container(self, command: list[str], *, expires_at: float) -> None:
        if len(command) < 2 or command[1] != "run":
            raise RuntimeError("Docker kernel command must begin with 'docker run'")
        create_command = [command[0], "create", *command[2:]]
        completed = subprocess.run(  # noqa: S603
            create_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(0.001, expires_at - time.time()),
            env=sanitized_docker_environment(),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-240:]
            raise RuntimeError(f"Docker GPU container creation failed: {detail or 'unknown error'}")

    def _copy_report(
        self,
        container_name: str,
        report_path: Path,
        completion: DockerSupervisorCompletion,
        *,
        timeout_seconds: float,
    ) -> None:
        copy_live_tmpfs_report(
            docker_binary=self.docker_binary,
            container_name=container_name,
            report_path=report_path,
            container_python=completion.supervisor_python,
            max_report_bytes=self.limits.max_report_bytes,
            timeout_seconds=min(_CLEANUP_TIMEOUT_SECONDS, timeout_seconds),
        )

    def _verify_copied_report(
        self,
        report_path: Path,
        report_root_identity: _process_control.FilesystemObjectIdentity,
        completion: DockerSupervisorCompletion,
    ) -> None:
        if completion.report_size is None or completion.report_sha256 is None:
            raise RuntimeError("Docker supervisor did not authenticate a report identity")
        payload = _process_control.read_bounded_regular_file(
            report_path,
            self.limits.max_report_bytes,
            expected_parent=report_root_identity,
            description="copied Docker supervisor report",
        )
        if len(payload) != completion.report_size or not secrets.compare_digest(
            hashlib.sha256(payload).hexdigest(),
            completion.report_sha256,
        ):
            raise RuntimeError("copied Docker supervisor report did not match its authenticated identity")

    def _read_report(
        self,
        report_path: Path,
        report_root_identity: _process_control.FilesystemObjectIdentity,
    ) -> dict[str, Any] | None:
        try:
            payload = _process_control.read_bounded_regular_file(
                report_path,
                self.limits.max_report_bytes,
                expected_parent=report_root_identity,
            )
            text = _validate_json_nesting(payload, self._report_limits)
            decoded = json.loads(text, parse_constant=_reject_json_constant)
            _validate_json_entries(decoded, self._report_limits)
            return decoded if isinstance(decoded, dict) else None
        except (FileNotFoundError, OSError, RecursionError, ValueError):
            return None

    def _resource_telemetry_outcome(
        self,
        payload: dict[str, Any],
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        attestation: DockerGPUDeviceAttestation,
    ) -> tuple[
        Literal["missing_resource_telemetry", "resource_exceeded", "resource_identity_mismatch"] | None,
        str | None,
    ]:
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            return "missing_resource_telemetry", "Benchmark report omitted mandatory CUDA resource telemetry"
        expected: dict[str, Any] = {
            "candidate_artifact_digest": candidate.artifact_digest,
            "incumbent_artifact_digest": incumbent.artifact_digest,
        }
        for name, identity in expected.items():
            if resources.get(name) != identity:
                return "resource_identity_mismatch", f"CUDA telemetry field {name} is identity-mismatched"
        hardware = payload.get("hardware")
        metadata = hardware.get("metadata") if isinstance(hardware, dict) else None
        expected_metadata = {
            "device_grant": attestation.device_id,
            "device_isolation_kind": attestation.isolation_kind,
            "device_enforced_memory_bytes": str(attestation.enforced_memory_bytes),
            "device_attestor_id": attestation.attestor_id,
            "device_attestation_digest": attestation.digest,
        }
        if not isinstance(metadata, dict) or any(
            metadata.get(name) != value for name, value in expected_metadata.items()
        ):
            return "resource_identity_mismatch", "CUDA telemetry hardware does not match the explicit GPU grant"
        names = (
            "candidate_peak_allocated_bytes",
            "candidate_peak_reserved_bytes",
            "incumbent_peak_allocated_bytes",
            "incumbent_peak_reserved_bytes",
            "device_total_memory_bytes",
        )
        if any(type(resources.get(name)) is not int or int(resources[name]) < 0 for name in names[:-1]) or (
            type(resources.get("device_total_memory_bytes")) is not int or int(resources["device_total_memory_bytes"]) <= 0
        ):
            return "missing_resource_telemetry", "CUDA allocation, reservation, and device-total metrics are mandatory"
        enforced_memory = attestation.enforced_memory_bytes
        if int(resources["device_total_memory_bytes"]) > enforced_memory:
            return "resource_identity_mismatch", "Reported CUDA capacity exceeds the verified GPU partition grant"
        peaks = [int(resources[name]) for name in names[:-1]]
        if max(peaks) > self.limits.max_gpu_memory_bytes:
            return "resource_exceeded", "CUDA peak allocation/reservation exceeded the enforced GPU partition limit"
        return None, None

    def _image_available(self) -> bool:
        completed = subprocess.run(  # noqa: S603
            [self.docker_binary, "image", "inspect", self.image],
            check=False,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            env=sanitized_docker_environment(),
        )
        return completed.returncode == 0

    def _container_oom(self, container_name: str, *, timeout_seconds: float = _CLEANUP_TIMEOUT_SECONDS) -> bool:
        completed = subprocess.run(  # noqa: S603
            [self.docker_binary, "inspect", "--format", "{{.State.OOMKilled}}", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(0.001, min(_CLEANUP_TIMEOUT_SECONDS, timeout_seconds)),
            env=sanitized_docker_environment(),
        )
        return completed.returncode == 0 and completed.stdout.strip().casefold() == "true"

    def _remove_container(self, container_name: str) -> None:
        completed = subprocess.run(  # noqa: S603
            [self.docker_binary, "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            env=sanitized_docker_environment(),
        )
        if completed.returncode != 0 and not docker_container_missing(completed):
            raise RuntimeError((completed.stderr or completed.stdout).strip()[-240:])

    def _verify_removed(self, container_name: str) -> None:
        completed = subprocess.run(  # noqa: S603
            [self.docker_binary, "ps", "-aq", "--filter", f"label={_OWNER_LABEL}={container_name}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            env=sanitized_docker_environment(),
        )
        if completed.stdout.strip():
            raise RuntimeError("container or escaped descendant remained after teardown")

    @staticmethod
    def _source_unchanged(path: Path, candidate: KernelCandidate) -> bool:
        try:
            return content_digest(path.read_bytes()) == candidate.source_digest
        except OSError:
            return False


def candidate_path_name(candidate: KernelCandidate, suffix: str, *, incumbent: bool = False) -> str:
    del candidate
    return f"{'incumbent' if incumbent else 'candidate'}{suffix}"


__all__ = [
    "DockerGPUDeviceAttestation",
    "DockerGPUDeviceAttestor",
    "DockerGPUDeviceGrant",
    "DockerKernelBenchmarkRunner",
    "DockerKernelWorkerLimits",
    "NvidiaSMIGPUDeviceAttestor",
]
