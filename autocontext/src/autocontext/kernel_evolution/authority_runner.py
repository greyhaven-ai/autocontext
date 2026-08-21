"""Docker composition for a trusted evaluator and isolated candidates.

The evaluator and each generated artifact run in distinct Docker PID, mount,
environment, and process namespaces.  The evaluator creates Unix sockets;
candidate containers receive read-only access to exactly one socket directory,
their own source, and explicitly public support paths.  Candidate containers
never mount private plans, references, report storage, or evaluator code.
"""

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
from dataclasses import asdict
from pathlib import Path
from typing import Any

from autocontext.execution.docker_isolation import (
    DockerIsolationLimits,
    build_docker_isolation_command,
    sanitized_docker_environment,
)
from autocontext.execution.scenario_remote_package import require_pinned_runtime_image
from autocontext.kernel_evolution import _process_control
from autocontext.kernel_evolution.authority_protocol import (
    KernelEvaluatorAuthorityReceipt,
    canonical_authority_digest,
    read_authority_hmac_secret,
    verify_authority_receipt,
)
from autocontext.kernel_evolution.authority_readiness import (
    protected_evaluator_boundary_error,
    protected_evaluator_boundary_requirements,
)
from autocontext.kernel_evolution.benchmark import (
    KernelBenchmarkExecution,
    _fingerprint_paths,
    _reject_json_constant,
)
from autocontext.kernel_evolution.docker_watchdog import (
    CLEANUP_TIMEOUT_SECONDS,
    DOCKER_KERNEL_OWNER_LABEL,
    create_docker_container,
    docker_container_missing,
    docker_image_available,
    is_unix_socket,
    launch_bounded_output_drains,
    launch_deadline_watchdog,
    remove_docker_container,
    start_attached_docker_container,
    terminate_attached_docker_processes,
    terminate_process_group,
    verify_docker_container_removed,
)
from autocontext.kernel_evolution.docker_worker import (
    DockerKernelWorkerLimits,
    _validate_json_entries,
    _validate_json_nesting,
)
from autocontext.kernel_evolution.gpu_attestation import (
    DockerGPUDeviceAttestation,
    DockerGPUDeviceAttestor,
    DockerGPUDeviceGrant,
    attest_partition_grant,
)
from autocontext.kernel_evolution.models import ARTIFACT_IDENTITY_VERSION, KernelCandidate, content_digest

PROTECTED_EVALUATOR_BOUNDARY = "trusted-evaluator/isolated-accelerator-candidate-v1"
_OWNER_LABEL = "ai.autocontext.kernel-authority-owner"
_EXPIRY_LABEL = "ai.autocontext.expires-at"
_ROLE_LABEL = "ai.autocontext.kernel-authority-role"
_SOCKET_NAME = "authority.sock"
_SOCKET_STARTUP_GRACE_SECONDS = 20.0
_AUTHORITY_HMAC_SECRET_CONTAINER_PATH = "/run/secrets/autocontext-authority-hmac"
_SAFE_AUTHORITY_HMAC_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


class DockerProtectedKernelBenchmarkRunner:
    """Run a trusted evaluator beside two isolated candidate authorities."""

    def __init__(
        self,
        evaluator_command: Sequence[str],
        *,
        image: str,
        container_python: str,
        evaluator_immutable_paths: Sequence[Path],
        evaluator_build_paths: Sequence[Path] | None = None,
        candidate_runtime_path: Path,
        candidate_support_paths: Sequence[Path] = (),
        gpu_grant: DockerGPUDeviceGrant,
        gpu_attestor: DockerGPUDeviceAttestor,
        authority_hmac_key_id: str,
        authority_hmac_secret_path: Path,
        limits: DockerKernelWorkerLimits | None = None,
        docker_binary: str = "docker",
        source_suffix: str = ".py",
        temporary_root: Path | None = None,
    ) -> None:
        if not evaluator_command or not evaluator_immutable_paths:
            raise ValueError("protected evaluation requires a command and evaluator-only immutable paths")
        if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", source_suffix) is None:
            raise ValueError("source_suffix must be a short safe extension")
        rendered = "\0".join(evaluator_command)
        required = ("{candidate_socket}", "{incumbent_socket}", "{report}")
        missing = [placeholder for placeholder in required if placeholder not in rendered]
        if missing:
            raise ValueError(f"protected evaluator command is missing placeholders: {', '.join(missing)}")
        require_pinned_runtime_image(image)
        resolved_binary = shutil.which(docker_binary)
        if resolved_binary is None:
            raise RuntimeError(f"Docker executable is unavailable: {docker_binary}")
        if not container_python.startswith("/") or ".." in Path(container_python).parts:
            raise ValueError("container_python must be an absolute normalized container path")
        if _SAFE_AUTHORITY_HMAC_KEY_ID.fullmatch(authority_hmac_key_id) is None:
            raise ValueError("authority_hmac_key_id must be a safe non-empty identifier")
        self._evaluator_command = tuple(evaluator_command)
        self.image = image
        self.container_python = container_python
        self.docker_binary = resolved_binary
        self._evaluator_paths = tuple(Path(path).resolve(strict=True) for path in evaluator_immutable_paths)
        self._evaluator_build_paths = tuple(
            Path(path).resolve(strict=True) for path in (evaluator_build_paths or evaluator_immutable_paths)
        )
        if not self._evaluator_build_paths or any(path not in self._evaluator_paths for path in self._evaluator_build_paths):
            raise ValueError("evaluator build paths must be a non-empty subset of immutable evaluator paths")
        self._candidate_runtime = candidate_runtime_path.resolve(strict=True)
        if not self._candidate_runtime.is_file():
            raise ValueError("candidate_runtime_path must be a regular file")
        self._candidate_support = tuple(Path(path).resolve(strict=True) for path in candidate_support_paths)
        secret_path = Path(authority_hmac_secret_path)
        self._authority_hmac_secret_path = secret_path if secret_path.is_absolute() else Path.cwd() / secret_path
        self._authority_hmac_secret = read_authority_hmac_secret(self._authority_hmac_secret_path)
        self._authority_hmac_secret_path = self._authority_hmac_secret_path.resolve(strict=True)
        self._authority_hmac_key_id = authority_hmac_key_id
        if any(self._authority_hmac_secret_path.is_relative_to(path) for path in self._evaluator_paths):
            raise ValueError("authority HMAC secret must use its dedicated evaluator-only mount")
        if self._candidate_runtime in (*self._evaluator_paths, self._authority_hmac_secret_path):
            raise ValueError("candidate runtime cannot be an evaluator-private immutable path")
        for support_path in self._candidate_support:
            for evaluator_path in (*self._evaluator_paths, self._authority_hmac_secret_path):
                if evaluator_path == support_path or evaluator_path.is_relative_to(support_path):
                    raise ValueError("candidate support paths cannot expose evaluator-private immutable material")
        self._evaluator_integrity_digest = _fingerprint_paths(self._evaluator_paths)
        self._evaluator_digest = _fingerprint_paths(self._evaluator_build_paths)
        self._candidate_boundary_digest = _fingerprint_paths((self._candidate_runtime, *self._candidate_support))
        self.gpu_grant = gpu_grant
        if not gpu_attestor.attestor_id.strip():
            raise ValueError("accelerator attestor must expose a non-empty identity")
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
        """Return public deployment identity without private-plan paths."""

        requirements = protected_evaluator_boundary_requirements()
        return {
            "kind": "docker-protected-accelerator-evaluator",
            "evidence_boundary": PROTECTED_EVALUATOR_BOUNDARY,
            "artifact_identity_version": ARTIFACT_IDENTITY_VERSION,
            "image": self.image,
            "container_python": self.container_python,
            "evaluator_command": list(self._evaluator_command),
            "evaluator_immutable_count": len(self._evaluator_paths),
            "evaluator_build_count": len(self._evaluator_build_paths),
            "evaluator_build_digest": self._evaluator_digest,
            "candidate_runtime_digest": self._candidate_boundary_digest,
            "candidate_support_count": len(self._candidate_support),
            "requested_accelerator_grant": asdict(self.gpu_grant),
            "accelerator_attestor": self.gpu_attestor.manifest(),
            "limits": asdict(self.limits),
            "candidate_mount_policy": "source+runtime+public-support+one-readonly-socket",
            "evaluator_mount_policy": "private-harness+channels+report;no-generated-source",
            "authority_authentication": {"algorithm": "hmac-sha256", "key_id": self._authority_hmac_key_id},
            "protected_boundary_requirements": requirements,
            "crash_safe_container_creation": requirements["crash_safe_container_creation"],
            "accelerator_role_isolation": requirements["accelerator_role_isolation"],
            "trusted_out_of_process_mutation_observation": requirements[
                "trusted_out_of_process_mutation_observation"
            ],
            "comparable_timing_boundaries": requirements["comparable_timing_boundaries"],
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
            return KernelBenchmarkExecution(returncode=None, error="candidate/incumbent suffix does not match runner")
        policy_error = protected_evaluator_boundary_error()
        if policy_error is not None:
            return KernelBenchmarkExecution(
                returncode=None,
                error=policy_error,
                outcome="resource_policy_unsupported",
            )
        attestation, attestation_error = self._attest_accelerator()
        if attestation_error is not None:
            return KernelBenchmarkExecution(
                returncode=None,
                error=attestation_error,
                outcome="resource_policy_unsupported",
            )
        assert attestation is not None
        if not self._immutable_boundary_unchanged():
            return KernelBenchmarkExecution(
                returncode=None,
                error="trusted evaluator or candidate runtime changed before launch",
                harness_unchanged=False,
            )
        try:
            self.reconcile()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return KernelBenchmarkExecution(
                returncode=None,
                error=f"authority orphan reconciliation failed: {type(exc).__name__}: {exc}",
                outcome="teardown_failed",
            )
        if not docker_image_available(self.docker_binary, self.image):
            return KernelBenchmarkExecution(returncode=None, error="pinned protected-evaluator image is unavailable")
        return self._run_session(candidate, incumbent, attestation, timeout_seconds=timeout_seconds)

    def _run_session(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        attestation: DockerGPUDeviceAttestation,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        with tempfile.TemporaryDirectory(prefix="autoctx-kernel-authority-", dir=self._temporary_root) as temp_name:
            root = Path(temp_name)
            candidate_source = self._stage_source(root / "candidate", candidate)
            incumbent_source = self._stage_source(root / "incumbent", incumbent)
            candidate_channel = root / "candidate-channel"
            incumbent_channel = root / "incumbent-channel"
            report_root = root / "report"
            for directory in (candidate_channel, incumbent_channel, report_root):
                directory.mkdir(mode=0o700)
            report_identity = _process_control.filesystem_object_identity(report_root.lstat())
            report_path = report_root / "report.json"
            session_id = uuid.uuid4().hex
            names = {
                "evaluator": f"autoctx-evaluator-{session_id[:18]}",
                "candidate": f"autoctx-candidate-{session_id[:18]}",
                "incumbent": f"autoctx-incumbent-{session_id[:18]}",
            }
            expires_at = time.time() + timeout_seconds + CLEANUP_TIMEOUT_SECONDS
            evaluator_command = self._evaluator_docker_command(
                names["evaluator"],
                candidate_channel,
                incumbent_channel,
                report_root,
                candidate,
                incumbent,
                attestation,
                expires_at,
            )
            candidate_command = self._candidate_docker_command(
                names["candidate"],
                candidate_source,
                candidate_channel,
                candidate,
                "candidate",
                attestation,
                expires_at,
            )
            incumbent_command = self._candidate_docker_command(
                names["incumbent"],
                incumbent_source,
                incumbent_channel,
                incumbent,
                "incumbent",
                attestation,
                expires_at,
            )
            execution = self._execute_authorities(
                evaluator_command,
                candidate_command,
                incumbent_command,
                names=names,
                socket_paths=(candidate_channel / _SOCKET_NAME, incumbent_channel / _SOCKET_NAME),
                report_path=report_path,
                report_identity=report_identity,
                timeout_seconds=timeout_seconds,
                accelerator_attestation=attestation,
                watchdog_root=root,
                expires_at=expires_at,
            )
            return KernelBenchmarkExecution(
                **{
                    **asdict(execution),
                    "harness_unchanged": self._immutable_boundary_unchanged(),
                    "candidate_unchanged": self._source_unchanged(candidate_source, candidate),
                    "incumbent_unchanged": self._source_unchanged(incumbent_source, incumbent),
                }
            )

    def _execute_authorities(
        self,
        evaluator_command: list[str],
        candidate_command: list[str],
        incumbent_command: list[str],
        *,
        names: dict[str, str],
        socket_paths: tuple[Path, Path],
        report_path: Path,
        report_identity: _process_control.FilesystemObjectIdentity,
        timeout_seconds: float,
        accelerator_attestation: DockerGPUDeviceAttestation,
        watchdog_root: Path,
        expires_at: float,
    ) -> KernelBenchmarkExecution:
        deadline = time.monotonic() + timeout_seconds
        evaluator: subprocess.Popen[bytes] | None = None
        candidates: list[subprocess.Popen[bytes]] = []
        watchdogs: dict[str, subprocess.Popen[bytes]] = {}
        stdout = _process_control.BoundedOutput()
        stderr = _process_control.BoundedOutput()
        drains: list[threading.Thread] = []
        quota_exceeded = threading.Event()
        quota_termination_started = threading.Event()
        lifecycle_lock = threading.Lock()
        timed_out = False
        returncode: int | None = None
        outcome = "complete"
        error: str | None = None
        payload: dict[str, Any] | None = None
        cleanup_errors: list[str] = []

        def terminate_for_output_quota() -> None:
            with lifecycle_lock:
                if quota_termination_started.is_set():
                    return
                quota_termination_started.set()
                for name in names.values():
                    try:
                        self._remove_container(name)
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        pass

        def launch(role: str, command: list[str], *, capture_output: bool) -> subprocess.Popen[bytes]:
            with lifecycle_lock:
                if quota_exceeded.is_set():
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
                create_docker_container(command, timeout_seconds=deadline - time.monotonic())
                watchdogs[role] = launch_deadline_watchdog(
                    self.docker_binary,
                    names[role],
                    expires_at,
                    watchdog_root / f"{role}-watchdog-ready",
                )
                return start_attached_docker_container(
                    self.docker_binary,
                    names[role],
                    capture_output=capture_output,
                )

        try:
            evaluator = launch("evaluator", evaluator_command, capture_output=True)
            drains = launch_bounded_output_drains(
                evaluator,
                max_output_bytes=self.limits.max_output_bytes,
                stdout=stdout,
                stderr=stderr,
                quota_exceeded=quota_exceeded,
                terminate=terminate_for_output_quota,
            )
            self._wait_for_evaluator_sockets(evaluator, socket_paths, deadline)
            for command in (candidate_command, incumbent_command):
                role = "candidate" if not candidates else "incumbent"
                candidates.append(launch(role, command, capture_output=False))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(evaluator_command, timeout_seconds)
            returncode = evaluator.wait(timeout=remaining)
            payload = self._read_report(report_path, report_identity)
            if returncode != 0:
                outcome = self._reported_failure_outcome(payload, default="evaluator_crashed")
                error = f"trusted evaluator exited with status {returncode}"
            elif payload is None:
                outcome = "protocol_corruption"
                error = "trusted evaluator exited without a valid bounded report"
            else:
                receipt_error = self._validate_receipt(payload, accelerator_attestation)
                if receipt_error is not None:
                    outcome = "protocol_corruption"
                    error = receipt_error
                else:
                    outcome = self._reported_failure_outcome(payload, default="complete")
        except subprocess.TimeoutExpired:
            timed_out = not quota_exceeded.is_set()
            outcome = "timeout" if timed_out else "resource_exceeded"
            error = (
                "protected evaluator authority session timed out"
                if timed_out
                else "protected evaluator diagnostic output exceeded its bounded channel"
            )
        except (OSError, RuntimeError, ValueError) as exc:
            outcome = "evaluator_crashed"
            error = f"protected evaluator authority session failed: {type(exc).__name__}: {exc}"
        finally:
            roles = ("candidate", "incumbent")[: len(candidates)]
            attached = [("evaluator", evaluator), *zip(roles, candidates, strict=True)]
            cleanup_errors.extend(terminate_attached_docker_processes(attached))
            for thread in drains:
                thread.join(timeout=CLEANUP_TIMEOUT_SECONDS)
            for role, name in names.items():
                try:
                    self._remove_container(name)
                    self._verify_removed(name)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    cleanup_errors.append(f"{name}: {type(exc).__name__}: {exc}")
                else:
                    watchdog = watchdogs.get(role)
                    if watchdog is not None:
                        try:
                            terminate_process_group(watchdog, description=f"{role} authority watchdog")
                        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                            cleanup_errors.append(f"{role} watchdog: {type(exc).__name__}: {exc}")
        if cleanup_errors:
            outcome = "teardown_failed"
            error = "; ".join(cleanup_errors)
        elif stdout.exceeded or stderr.exceeded or stdout.read_failed or stderr.read_failed:
            outcome = "resource_exceeded"
            error = "protected evaluator diagnostic output exceeded its bounded channel"
        return KernelBenchmarkExecution(
            returncode=returncode,
            timed_out=timed_out,
            report_payload=payload,
            stdout=stdout.text,
            stderr=stderr.text,
            stdout_truncated=stdout.exceeded or stdout.read_failed,
            stderr_truncated=stderr.exceeded or stderr.read_failed,
            error=error,
            outcome=outcome,  # type: ignore[arg-type]
        )

    def _evaluator_docker_command(
        self,
        container_name: str,
        candidate_channel: Path,
        incumbent_channel: Path,
        report_root: Path,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        attestation: DockerGPUDeviceAttestation,
        expires_at: float,
    ) -> list[str]:
        replacements = self._identity_replacements(candidate, incumbent)
        replacements.update(
            {
                "{candidate_socket}": f"/channels/candidate/{_SOCKET_NAME}",
                "{incumbent_socket}": f"/channels/incumbent/{_SOCKET_NAME}",
                "{report}": "/output/report.json",
            }
        )
        for index in range(len(self._evaluator_paths)):
            replacements[f"{{immutable_{index}}}"] = f"/evaluator/{index}"
        argv = [self._replace(raw, replacements) for raw in self._evaluator_command]
        return self._isolation_command(
            container_name=container_name,
            role="evaluator",
            attestation=attestation,
            expires_at=expires_at,
            readonly_mounts={
                **{path: f"/evaluator/{index}" for index, path in enumerate(self._evaluator_paths)},
                self._authority_hmac_secret_path: _AUTHORITY_HMAC_SECRET_CONTAINER_PATH,
            },
            writable_mounts={
                candidate_channel: "/channels/candidate",
                incumbent_channel: "/channels/incumbent",
                report_root: "/output",
            },
            argv=argv,
            extra_environment={
                "AUTOCONTEXT_EVALUATOR_BUILD_DIGEST": self._evaluator_digest,
                "AUTOCONTEXT_BOUNDARY_MANIFEST_DIGEST": canonical_authority_digest(self.manifest()),
                "AUTOCONTEXT_AUTHORITY_HMAC_KEY_ID": self._authority_hmac_key_id,
                "AUTOCONTEXT_AUTHORITY_HMAC_SECRET_FILE": _AUTHORITY_HMAC_SECRET_CONTAINER_PATH,
            },
        )

    def _candidate_docker_command(
        self,
        container_name: str,
        source_path: Path,
        channel_path: Path,
        artifact: KernelCandidate,
        role: str,
        attestation: DockerGPUDeviceAttestation,
        expires_at: float,
    ) -> list[str]:
        argv = [
            self.container_python,
            "-I",
            "-B",
            "/authority/worker.py",
            "--connect",
            f"/channel/{_SOCKET_NAME}",
            "--source",
            f"/artifact/source{self._source_suffix}",
            "--entrypoint",
            artifact.entrypoint,
            "--artifact-digest",
            artifact.artifact_digest,
            "--role",
            role,
        ]
        for index in range(len(self._candidate_support)):
            argv.extend(("--support-path", f"/support/{index}"))
        readonly_mounts = {
            self._candidate_runtime: "/authority/worker.py",
            source_path: f"/artifact/source{self._source_suffix}",
            channel_path: "/channel",
        }
        readonly_mounts.update({path: f"/support/{index}" for index, path in enumerate(self._candidate_support)})
        return self._isolation_command(
            container_name=container_name,
            role=role,
            attestation=attestation,
            expires_at=expires_at,
            readonly_mounts=readonly_mounts,
            writable_mounts={},
            argv=argv,
        )

    def _isolation_command(
        self,
        *,
        container_name: str,
        role: str,
        attestation: DockerGPUDeviceAttestation,
        expires_at: float,
        readonly_mounts: dict[Path, str],
        writable_mounts: dict[Path, str],
        argv: list[str],
        extra_environment: dict[str, str] | None = None,
    ) -> list[str]:
        environment = {
            "AUTOCONTEXT_ACCELERATOR_DEVICE_ID": attestation.device_id,
            "AUTOCONTEXT_ACCELERATOR_ISOLATION_KIND": attestation.isolation_kind,
            "AUTOCONTEXT_ACCELERATOR_ENFORCED_MEMORY_BYTES": str(attestation.enforced_memory_bytes),
            "AUTOCONTEXT_ACCELERATOR_ATTESTOR_ID": attestation.attestor_id,
            "AUTOCONTEXT_ACCELERATOR_ATTESTATION_DIGEST": attestation.digest,
            # Compatibility aliases for the existing CUDA profile reader.
            "AUTOCONTEXT_GPU_DEVICE_ID": attestation.device_id,
            "AUTOCONTEXT_GPU_ISOLATION_KIND": attestation.isolation_kind,
            "AUTOCONTEXT_GPU_ENFORCED_MEMORY_BYTES": str(attestation.enforced_memory_bytes),
            "AUTOCONTEXT_GPU_ATTESTOR_ID": attestation.attestor_id,
            "AUTOCONTEXT_GPU_ATTESTATION_DIGEST": attestation.digest,
        }
        environment.update(extra_environment or {})
        return build_docker_isolation_command(
            docker_binary=self.docker_binary,
            image=self.image,
            container_name=container_name,
            labels={
                _OWNER_LABEL: container_name,
                DOCKER_KERNEL_OWNER_LABEL: container_name,
                _EXPIRY_LABEL: f"{expires_at:.6f}",
                _ROLE_LABEL: role,
            },
            limits=DockerIsolationLimits(
                memory_mb=self.limits.memory_mb,
                cpu_count=self.limits.cpu_count,
                cpu_time_seconds=self.limits.cpu_time_seconds,
                pids_limit=self.limits.pids_limit,
            ),
            readonly_mounts=readonly_mounts,
            writable_mounts=writable_mounts,
            tmpfs_mounts={
                "/tmp": "rw,noexec,nosuid,nodev,size=64m,nr_inodes=1024",
                "/workspace": (
                    f"rw,nosuid,nodev,exec,size={self.limits.max_workspace_bytes},nr_inodes={self.limits.max_workspace_inodes}"
                ),
            },
            argv=argv,
            gpu_device=attestation.device_id,
            auto_remove=False,
            working_dir="/workspace",
            environment=environment,
        )

    def reconcile(self, *, now: float | None = None) -> int:
        """Remove expired evaluator/candidate containers after coordinator loss."""

        listed = subprocess.run(  # noqa: S603
            [self.docker_binary, "ps", "-aq", "--filter", f"label={_OWNER_LABEL}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=CLEANUP_TIMEOUT_SECONDS,
            env=sanitized_docker_environment(),
        )
        container_ids = tuple(value for value in listed.stdout.splitlines() if value.strip())
        current = time.time() if now is None else now
        expired: list[str] = []
        for container_id in container_ids:
            inspected = subprocess.run(  # noqa: S603
                [
                    self.docker_binary,
                    "inspect",
                    "--format",
                    f'{{{{.Id}}}}\t{{{{ index .Config.Labels "{_EXPIRY_LABEL}" }}}}',
                    container_id,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=CLEANUP_TIMEOUT_SECONDS,
                env=sanitized_docker_environment(),
            )
            if inspected.returncode != 0:
                if docker_container_missing(inspected):
                    continue
                raise RuntimeError("authority container inspection failed")
            container, separator, raw_expiry = inspected.stdout.strip().partition("\t")
            try:
                expiry = float(raw_expiry)
            except ValueError as exc:
                raise RuntimeError("owned authority container has invalid expiry metadata") from exc
            if not separator or not math.isfinite(expiry) or expiry <= 0:
                raise RuntimeError("owned authority container has invalid expiry metadata")
            if expiry <= current:
                expired.append(container)
        for container_id in expired:
            self._remove_container(container_id)
            self._verify_removed(container_id)
        return len(expired)

    def _attest_accelerator(self) -> tuple[DockerGPUDeviceAttestation | None, str | None]:
        try:
            return (
                attest_partition_grant(
                    self.gpu_grant,
                    self.gpu_attestor,
                    max_gpu_memory_bytes=self.limits.max_gpu_memory_bytes,
                ),
                None,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            return None, f"trusted accelerator attestation failed: {type(exc).__name__}: {exc}"

    def _immutable_boundary_unchanged(self) -> bool:
        try:
            return (
                _fingerprint_paths(self._evaluator_paths) == self._evaluator_integrity_digest
                and _fingerprint_paths((self._candidate_runtime, *self._candidate_support))
                == self._candidate_boundary_digest
            )
        except (OSError, ValueError):
            return False

    def _wait_for_evaluator_sockets(
        self,
        evaluator: subprocess.Popen[bytes],
        paths: tuple[Path, Path],
        deadline: float,
    ) -> None:
        startup_deadline = min(deadline, time.monotonic() + _SOCKET_STARTUP_GRACE_SECONDS)
        while time.monotonic() < startup_deadline:
            if evaluator.poll() is not None:
                raise RuntimeError("trusted evaluator exited before creating authority sockets")
            if all(is_unix_socket(path) for path in paths):
                return
            time.sleep(0.01)
        raise RuntimeError("trusted evaluator did not create bounded authority sockets before startup deadline")

    def _read_report(
        self,
        report_path: Path,
        report_identity: _process_control.FilesystemObjectIdentity,
    ) -> dict[str, Any] | None:
        try:
            payload = _process_control.read_bounded_regular_file(
                report_path,
                self._report_limits.max_bytes,
                expected_parent=report_identity,
                description="protected evaluator report",
            )
            text = _validate_json_nesting(payload, self._report_limits)
            decoded = json.loads(text, parse_constant=_reject_json_constant)
            _validate_json_entries(decoded, self._report_limits)
            return decoded if isinstance(decoded, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, OSError, RecursionError, ValueError):
            return None

    def _validate_receipt(
        self,
        payload: dict[str, Any],
        attestation: DockerGPUDeviceAttestation,
    ) -> str | None:
        try:
            receipt = KernelEvaluatorAuthorityReceipt.model_validate(payload.get("evaluator_authority_receipt"))
            verify_authority_receipt(
                receipt,
                payload,
                trusted_key_id=self._authority_hmac_key_id,
                trusted_secret=self._authority_hmac_secret,
                expected_evaluator_build_digest=self._evaluator_digest,
                expected_boundary_manifest_digest=canonical_authority_digest(self.manifest()),
            )
        except (TypeError, ValueError):
            return "trusted evaluator report omitted or forged its authority receipt"
        accelerator = receipt.accelerator_attestation
        if (
            accelerator.device_id != attestation.device_id
            or accelerator.isolation_kind != attestation.isolation_kind
            or accelerator.enforced_memory_bytes != attestation.enforced_memory_bytes
            or accelerator.attestor_id != attestation.attestor_id
            or accelerator.metadata.get("grant_attestation_digest") != attestation.digest
        ):
            return "trusted evaluator receipt does not match the host-attested accelerator grant"
        return None

    @staticmethod
    def _reported_failure_outcome(payload: dict[str, Any] | None, *, default: str) -> str:
        failure = payload.get("failure_kind") if payload is not None else None
        if not isinstance(failure, str):
            return default
        return {
            "oom": "oom",
            "protocol_corruption": "protocol_corruption",
            "evaluator_crash": "evaluator_crashed",
            "candidate_crash": "candidate_crashed",
            "teardown_failure": "teardown_failed",
            "timeout": "timeout",
        }.get(failure, default)

    @staticmethod
    def _identity_replacements(candidate: KernelCandidate, incumbent: KernelCandidate) -> dict[str, str]:
        return {
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

    @staticmethod
    def _replace(raw: str, replacements: dict[str, str]) -> str:
        value = raw
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        return value

    @staticmethod
    def _stage_source(root: Path, artifact: KernelCandidate) -> Path:
        root.mkdir(mode=0o700)
        path = root / f"source{artifact.source_suffix}"
        path.write_bytes(artifact.source_bytes)
        path.chmod(0o444)
        return path

    @staticmethod
    def _source_unchanged(path: Path, artifact: KernelCandidate) -> bool:
        try:
            return content_digest(path.read_bytes()) == artifact.source_digest
        except OSError:
            return False

    def _remove_container(self, container_name: str) -> None:
        remove_docker_container(self.docker_binary, container_name)

    def _verify_removed(self, container_name: str) -> None:
        verify_docker_container_removed(self.docker_binary, container_name)


__all__ = ["PROTECTED_EVALUATOR_BOUNDARY", "DockerProtectedKernelBenchmarkRunner"]
