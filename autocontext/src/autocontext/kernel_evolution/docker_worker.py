"""OS-isolated Docker GPU benchmark worker (AC-991)."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
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
    KernelBenchmarkExecutionOutcome,
    _fingerprint_paths,
    _reject_json_constant,
)
from autocontext.kernel_evolution.models import ARTIFACT_IDENTITY_VERSION, KernelCandidate, content_digest

GPUIsolationKind = Literal["mig", "hardware-partition", "visibility-only"]
_OWNER_LABEL = "ai.autocontext.kernel-worker"
_EXPIRY_LABEL = "ai.autocontext.expires-at"
_CLEANUP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class DockerGPUDeviceGrant:
    """An explicit GPU grant and its independently verified memory boundary."""

    device_id: str
    isolation_kind: GPUIsolationKind
    enforced_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.device_id.strip()
            or self.device_id.strip().casefold() == "all"
            or re.fullmatch(r"[A-Za-z0-9_.:/-]+", self.device_id) is None
        ):
            raise ValueError("GPU device grant must name one explicit device or partition")
        if any(char in self.device_id for char in "\r\n\0"):
            raise ValueError("GPU device grant contains forbidden control characters")
        if self.isolation_kind not in {"mig", "hardware-partition", "visibility-only"}:
            raise ValueError(f"unknown GPU isolation kind: {self.isolation_kind}")
        if self.isolation_kind in {"mig", "hardware-partition"}:
            if self.enforced_memory_bytes is None or self.enforced_memory_bytes < 1:
                raise ValueError("partitioned GPU grants require a verified enforced_memory_bytes capacity")
        elif self.enforced_memory_bytes is not None:
            raise ValueError("visibility-only GPU grants cannot claim a memory enforcement boundary")


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
        self.gpu_grant = gpu_grant
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
            "command": list(self._command),
            "immutable_paths": [str(path) for path in self._immutable_paths],
            "immutable_harness_digest": self._harness_digest,
            "gpu_grant": asdict(self.gpu_grant),
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
        policy_error = self._gpu_enforcement_error()
        if policy_error is not None:
            return KernelBenchmarkExecution(
                returncode=None,
                error=policy_error,
                outcome="resource_policy_unsupported",
            )
        try:
            if _fingerprint_paths(self._immutable_paths) != self._harness_digest:
                return KernelBenchmarkExecution(
                    returncode=None,
                    error="immutable benchmark harness changed before container launch",
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
            command = self._docker_command(
                container_name,
                input_root,
                report_root,
                candidate,
                incumbent,
                timeout_seconds,
            )
            execution = self._execute_container(
                command,
                container_name=container_name,
                report_path=report_path,
                report_root_identity=report_root_identity,
                timeout_seconds=timeout_seconds,
            )
            payload = execution.report_payload
            if payload is not None and execution.outcome == "complete":
                outcome, detail = self._resource_telemetry_outcome(payload, candidate, incumbent)
                if outcome is not None:
                    execution = KernelBenchmarkExecution(
                        **{
                            **asdict(execution),
                            "outcome": outcome,
                            "error": detail,
                        }
                    )
            try:
                harness_unchanged = _fingerprint_paths(self._immutable_paths) == self._harness_digest
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

    def _gpu_enforcement_error(self) -> str | None:
        enforced = self.gpu_grant.enforced_memory_bytes
        if enforced is None:
            return "GPU memory enforcement is unavailable; use a verified MIG or hardware partition grant"
        if enforced > self.limits.max_gpu_memory_bytes:
            return f"GPU partition capacity {enforced} exceeds configured hard limit {self.limits.max_gpu_memory_bytes} bytes"
        return None

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
        inspected = subprocess.run(  # noqa: S603
            [
                self.docker_binary,
                "inspect",
                "--format",
                f'{{{{.Id}}}}\t{{{{ index .Config.Labels "{_EXPIRY_LABEL}" }}}}',
                *container_ids,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            env=sanitized_docker_environment(),
        )
        deadline = time.time() if now is None else now
        expired: list[str] = []
        for line in inspected.stdout.splitlines():
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
        report_root: Path,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        timeout_seconds: float,
    ) -> list[str]:
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
        expiry = time.time() + timeout_seconds + _CLEANUP_TIMEOUT_SECONDS
        readonly_mounts = {input_root: "/input"}
        readonly_mounts.update({path: f"/benchmark/{index}" for index, path in enumerate(self._immutable_paths)})
        return build_docker_isolation_command(
            docker_binary=self.docker_binary,
            image=self.image,
            container_name=container_name,
            labels={_OWNER_LABEL: container_name, _EXPIRY_LABEL: f"{expiry:.6f}"},
            limits=DockerIsolationLimits(
                memory_mb=self.limits.memory_mb,
                cpu_count=self.limits.cpu_count,
                cpu_time_seconds=self.limits.cpu_time_seconds,
                pids_limit=self.limits.pids_limit,
            ),
            readonly_mounts=readonly_mounts,
            writable_mounts={report_root: "/output"},
            tmpfs_mounts={
                "/tmp": "rw,noexec,nosuid,nodev,size=64m,nr_inodes=1024",
                "/workspace": (
                    f"rw,nosuid,nodev,exec,size={self.limits.max_workspace_bytes},nr_inodes={self.limits.max_workspace_inodes}"
                ),
            },
            argv=argv,
            gpu_device=self.gpu_grant.device_id,
            auto_remove=False,
            working_dir="/workspace",
            ulimits={"fsize": (self.limits.max_report_bytes, self.limits.max_report_bytes)},
            environment={"AUTOCONTEXT_GPU_DEVICE_ID": self.gpu_grant.device_id},
        )

    def _execute_container(
        self,
        command: list[str],
        *,
        container_name: str,
        report_path: Path,
        report_root_identity: _process_control.FilesystemObjectIdentity,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        stdout = _process_control.BoundedOutput()
        stderr = _process_control.BoundedOutput()
        quota_exceeded = threading.Event()
        report_stop = threading.Event()
        report_errors: list[str] = []
        threads: list[threading.Thread] = []
        timed_out = False
        returncode: int | None = None
        error: str | None = None
        oom = False
        cleanup_error: str | None = None
        proc: subprocess.Popen[bytes] | None = None

        def terminate() -> None:
            self._remove_container(container_name)

        try:
            proc = subprocess.Popen(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                env=sanitized_docker_environment(),
            )
            assert proc.stdout is not None and proc.stderr is not None
            for stream, result in ((proc.stdout, stdout), (proc.stderr, stderr)):
                thread = threading.Thread(
                    target=_process_control.drain_bounded,
                    args=(stream, self.limits.max_output_bytes, result, quota_exceeded, terminate),
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
            monitor = threading.Thread(
                target=_process_control.monitor_report,
                args=(
                    report_path,
                    self._report_limits,
                    report_root_identity,
                    report_stop,
                    report_errors,
                    quota_exceeded,
                    terminate,
                ),
                daemon=True,
            )
            monitor.start()
            threads.append(monitor)
            try:
                returncode = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = not quota_exceeded.is_set()
                terminate()
                proc.wait(timeout=_CLEANUP_TIMEOUT_SECONDS)
            oom = self._container_oom(container_name)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            error = f"Docker GPU worker failed: {type(exc).__name__}: {exc}"
        finally:
            report_stop.set()
            for thread in threads:
                thread.join(timeout=_process_control.CLEANUP_JOIN_TIMEOUT_SECONDS)
            if proc is not None:
                returncode = proc.returncode
                for pipe in (proc.stdout, proc.stderr):
                    if pipe is not None:
                        pipe.close()
            try:
                self._remove_container(container_name)
                self._verify_removed(container_name)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                cleanup_error = f"Docker GPU worker teardown failed: {type(exc).__name__}: {exc}"
        quota_details = []
        if stdout.exceeded:
            quota_details.append(f"stdout exceeded max_output_bytes={self.limits.max_output_bytes}")
        if stderr.exceeded:
            quota_details.append(f"stderr exceeded max_output_bytes={self.limits.max_output_bytes}")
        quota_details.extend(report_errors)
        payload = self._read_report(report_path, report_root_identity) if not timed_out else None
        outcome: KernelBenchmarkExecutionOutcome = "complete"
        if cleanup_error is not None:
            outcome, error = "teardown_failed", cleanup_error
        elif oom:
            outcome, error = "oom", "Docker reported an out-of-memory kill"
        elif quota_exceeded.is_set() or quota_details:
            outcome, error = "resource_exceeded", "; ".join(quota_details) or "worker resource quota exceeded"
        elif timed_out:
            outcome, error = "timeout", f"Docker GPU worker timed out after {timeout_seconds:g}s"
        return KernelBenchmarkExecution(
            returncode=returncode,
            timed_out=timed_out,
            report_payload=payload,
            stdout=stdout.text,
            stderr=stderr.text,
            stdout_truncated=stdout.exceeded or stdout.read_failed,
            stderr_truncated=stderr.exceeded or stderr.read_failed,
            error=error,
            outcome=outcome,
        )

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
            decoded = json.loads(payload.decode("utf-8"), parse_constant=_reject_json_constant)
            return decoded if isinstance(decoded, dict) else None
        except (FileNotFoundError, OSError, ValueError):
            return None

    def _resource_telemetry_outcome(
        self,
        payload: dict[str, Any],
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
    ) -> tuple[
        Literal["missing_resource_telemetry", "resource_exceeded", "resource_identity_mismatch"] | None,
        str | None,
    ]:
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            return "missing_resource_telemetry", "Benchmark report omitted mandatory CUDA resource telemetry"
        expected: dict[str, object] = {
            "candidate_artifact_digest": candidate.artifact_digest,
            "incumbent_artifact_digest": incumbent.artifact_digest,
        }
        for name, identity in expected.items():
            if resources.get(name) != identity:
                return "resource_identity_mismatch", f"CUDA telemetry field {name} is identity-mismatched"
        hardware = payload.get("hardware")
        metadata = hardware.get("metadata") if isinstance(hardware, dict) else None
        if not isinstance(metadata, dict) or metadata.get("device_grant") != self.gpu_grant.device_id:
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
        enforced_memory = self.gpu_grant.enforced_memory_bytes
        if enforced_memory is None or int(resources["device_total_memory_bytes"]) > enforced_memory:
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

    def _container_oom(self, container_name: str) -> bool:
        completed = subprocess.run(  # noqa: S603
            [self.docker_binary, "inspect", "--format", "{{.State.OOMKilled}}", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
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
        if completed.returncode != 0 and "No such container" not in completed.stderr:
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


__all__ = ["DockerGPUDeviceGrant", "DockerKernelBenchmarkRunner", "DockerKernelWorkerLimits"]
