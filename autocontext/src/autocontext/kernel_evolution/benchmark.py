"""External benchmark boundary and report validation for kernel evolution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn, Protocol

from pydantic import ValidationError

from autocontext.kernel_evolution import _process_control
from autocontext.kernel_evolution.benchmark_authority import BenchmarkAuthorityVerifier
from autocontext.kernel_evolution.evaluator_config import KernelBenchmarkEvaluatorConfig
from autocontext.kernel_evolution.models import (
    ARTIFACT_IDENTITY_VERSION,
    KernelBenchmarkObservation,
    KernelBenchmarkReport,
    KernelCandidate,
    content_digest,
)
from autocontext.kernel_evolution.promotion_statistics import bootstrap_lcb, geometric_mean_ratio, percentile
from autocontext.kernel_evolution.resource_policy import evaluate_kernel_resource_policy

_WINDOWS_LAUNCH_GATE = b"\x01"
KernelBenchmarkExecutionOutcome = Literal[
    "complete", "timeout", "oom", "resource_exceeded", "resource_policy_unsupported",
    "missing_resource_telemetry", "resource_identity_mismatch", "protocol_corruption",
    "evaluator_crashed", "candidate_crashed", "teardown_failed",
]


@dataclass(frozen=True, slots=True)
class KernelBenchmarkExecution:
    """Raw process outcome. JSON is parsed only from the runner-owned report file."""

    returncode: int | None
    timed_out: bool = False
    report_payload: dict[str, Any] | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    harness_unchanged: bool = True
    candidate_unchanged: bool = True
    incumbent_unchanged: bool = True
    outcome: KernelBenchmarkExecutionOutcome = "complete"


class KernelBenchmarkRunner(Protocol):
    """Host-owned data plane used by :class:`KernelBenchmarkEvaluator`."""

    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution: ...

    def manifest(self) -> dict[str, Any]: ...


def _update_fingerprint_frame(digest: Any, frame_type: bytes, *fields: bytes) -> None:
    digest.update(len(frame_type).to_bytes(4, "big"))
    digest.update(frame_type)
    digest.update(len(fields).to_bytes(4, "big"))
    for field in fields:
        digest.update(len(field).to_bytes(8, "big"))
        digest.update(field)


def _lexical_absolute_path(supplied: Path) -> Path:
    if ".." in supplied.parts:
        raise ValueError(f"immutable benchmark path must not contain '..': {supplied}")
    return supplied if supplied.is_absolute() else Path.cwd() / supplied


def _lstat_without_symlink_components(path: Path) -> os.stat_result:
    current = Path(path.anchor)
    try:
        current_stat = current.lstat()
        for component in path.parts[1:]:
            current /= component
            current_stat = current.lstat()
            if stat.S_ISLNK(current_stat.st_mode):
                raise ValueError(f"immutable benchmark path cannot contain symlinks: {current}")
    except FileNotFoundError as exc:
        raise ValueError(f"immutable benchmark path must exist: {current}") from exc
    return current_stat


def _digest_regular_file(path: Path, expected: os.stat_result) -> tuple[int, bytes]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode) or _process_control.filesystem_snapshot_identity(
            opened_before
        ) != _process_control.filesystem_snapshot_identity(expected):
            raise ValueError(f"immutable benchmark file changed while fingerprinting: {path}")
        file_digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            size += len(chunk)
            file_digest.update(chunk)
        opened_after = os.fstat(descriptor)
        current = path.lstat()
        if _process_control.filesystem_snapshot_identity(opened_before) != _process_control.filesystem_snapshot_identity(
            opened_after
        ) or _process_control.filesystem_snapshot_identity(opened_after) != _process_control.filesystem_snapshot_identity(
            current
        ):
            raise ValueError(f"immutable benchmark file changed while fingerprinting: {path}")
        return size, file_digest.digest()
    finally:
        os.close(descriptor)


def _relative_label(components: tuple[str, ...]) -> bytes:
    encoded = bytearray(len(components).to_bytes(4, "big"))
    for component in components:
        raw = os.fsencode(component)
        encoded.extend(len(raw).to_bytes(8, "big"))
        encoded.extend(raw)
    return bytes(encoded)


def _fingerprint_tree(digest: Any, root_index: int, path: Path, components: tuple[str, ...], entry_stat: os.stat_result) -> None:
    index = root_index.to_bytes(8, "big")
    label = _relative_label(components)
    if stat.S_ISLNK(entry_stat.st_mode):
        raise ValueError(f"immutable benchmark tree cannot contain symlinks: {path}")
    if stat.S_ISREG(entry_stat.st_mode):
        size, content = _digest_regular_file(path, entry_stat)
        _update_fingerprint_frame(digest, b"file", index, label, size.to_bytes(16, "big"), content)
        return
    if not stat.S_ISDIR(entry_stat.st_mode):
        raise ValueError(f"immutable benchmark tree may contain only regular files and directories: {path}")

    directory_before = _process_control.filesystem_snapshot_identity(entry_stat)
    _update_fingerprint_frame(digest, b"directory", index, label)
    with os.scandir(path) as iterator:
        entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
    for entry in entries:
        child_path = path / entry.name
        child_stat = entry.stat(follow_symlinks=False)
        _fingerprint_tree(digest, root_index, child_path, (*components, entry.name), child_stat)
    if _process_control.filesystem_snapshot_identity(path.lstat()) != directory_before:
        raise ValueError(f"immutable benchmark directory changed while fingerprinting: {path}")


def _fingerprint_paths(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    _update_fingerprint_frame(digest, b"format", b"autocontext-immutable-tree-v2")
    roots = sorted((_lexical_absolute_path(path) for path in paths), key=lambda path: os.fsencode(os.fspath(path)))
    _update_fingerprint_frame(digest, b"root-count", len(roots).to_bytes(8, "big"))
    for root_index, root in enumerate(roots):
        root_stat = _lstat_without_symlink_components(root)
        if stat.S_ISREG(root_stat.st_mode):
            root_type = b"file"
        elif stat.S_ISDIR(root_stat.st_mode):
            root_type = b"directory"
        else:
            raise ValueError(f"immutable benchmark path must be a regular file or directory: {root}")
        _update_fingerprint_frame(digest, b"root", root_index.to_bytes(8, "big"), os.fsencode(os.fspath(root)), root_type)
        _fingerprint_tree(digest, root_index, root, (), root_stat)
    return f"sha256:{digest.hexdigest()}"


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant {value}")


class ExternalKernelBenchmarkRunner:
    """Run a trusted benchmark adapter without a shell.

    The command must write a JSON report to ``{report}``; stdout is never
    interpreted as a score. Generated source is run only by this child process,
    never imported into the AutoContext control process.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        source_suffix: str = ".py",
        immutable_paths: Sequence[Path] = (),
        environment: Mapping[str, str] | None = None,
        max_output_bytes: int = 64_000,
        max_report_bytes: int = 2_000_000,
        max_report_entries: int = 1_024,
        max_report_depth: int = 16,
        temporary_root: Path | None = None,
        trusted_unsafe: bool = False,
    ) -> None:
        if not trusted_unsafe:
            raise PermissionError("local kernel execution requires trusted_unsafe=True; use an OS-isolated runner")
        if not command:
            raise ValueError("benchmark command must not be empty")
        if not immutable_paths:
            raise ValueError("immutable_paths must pin the benchmark adapter and problem contract")
        rendered = "\0".join(command)
        missing = [placeholder for placeholder in ("{candidate}", "{incumbent}", "{report}") if placeholder not in rendered]
        if missing:
            raise ValueError(f"benchmark command is missing required placeholders: {', '.join(missing)}")
        executable = str(command[0])
        resolved_executable = executable if Path(executable).is_absolute() else shutil.which(executable)
        if not resolved_executable:
            raise ValueError(f"benchmark executable was not found: {executable}")
        if max_output_bytes < 1 or max_report_bytes < 1:
            raise ValueError("output and report byte limits must be positive")

        # Preserve the invocation path instead of replacing it with the final
        # symlink target. Python virtual environments commonly expose
        # ``bin/python`` as a symlink; invoking the target directly bypasses
        # ``pyvenv.cfg`` and loses the environment's installed packages.
        executable_path = Path(os.path.abspath(os.fspath(resolved_executable)))
        if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            raise ValueError(f"benchmark executable is not an executable file: {executable_path}")
        self._command = (str(executable_path), *tuple(command[1:]))
        self._executable_target = str(executable_path.resolve(strict=True))
        self._cwd = cwd.resolve() if cwd is not None else None
        self._source_suffix = source_suffix
        self._immutable_paths = tuple(_lexical_absolute_path(Path(path)) for path in immutable_paths)
        self._harness_digest = _fingerprint_paths(self._immutable_paths)
        if sys.platform == "win32":
            launcher_path = Path(__file__).with_name("_windows_job_launcher.py").resolve(strict=True)
            self._windows_launcher_path: Path | None = launcher_path
            self._windows_launcher_digest: str | None = _fingerprint_paths((launcher_path,))
        else:
            self._windows_launcher_path = None
            self._windows_launcher_digest = None
        self._environment = dict(environment or {})
        self._max_output_bytes = max_output_bytes
        self._report_limits = _process_control.ReportLimits(
            max_bytes=max_report_bytes,
            max_entries=max_report_entries,
            max_depth=max_report_depth,
        )
        self._temporary_root = temporary_root

    def manifest(self) -> dict[str, Any]:
        return {
            "kind": "external-command",
            "trusted_unsafe": True,
            "artifact_identity_version": ARTIFACT_IDENTITY_VERSION,
            "command": list(self._command),
            "executable_target": self._executable_target,
            "cwd": str(self._cwd) if self._cwd is not None else None,
            "source_suffix": self._source_suffix,
            "immutable_harness_digest": self._harness_digest,
            "immutable_paths": [str(path) for path in self._immutable_paths],
            "windows_launcher_path": str(self._windows_launcher_path) if self._windows_launcher_path is not None else None,
            "windows_launcher_digest": self._windows_launcher_digest,
            "max_output_bytes": self._max_output_bytes,
            "max_report_bytes": self._report_limits.max_bytes,
            "max_report_entries": self._report_limits.max_entries,
            "max_report_depth": self._report_limits.max_depth,
        }

    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if candidate.source_suffix != self._source_suffix or incumbent.source_suffix != self._source_suffix:
            return KernelBenchmarkExecution(
                returncode=None,
                error=(
                    f"candidate/incumbent suffix must match external runner suffix {self._source_suffix!r}; "
                    f"got {candidate.source_suffix!r} and {incumbent.source_suffix!r}"
                ),
            )
        try:
            if str(Path(self._command[0]).resolve(strict=True)) != self._executable_target:
                return KernelBenchmarkExecution(
                    returncode=None,
                    error="benchmark executable target changed before evaluation",
                    harness_unchanged=False,
                )
        except OSError as exc:
            return KernelBenchmarkExecution(
                returncode=None,
                error=f"benchmark executable failed preflight: {exc}",
                harness_unchanged=False,
            )
        try:
            if _fingerprint_paths(self._immutable_paths) != self._harness_digest:
                return KernelBenchmarkExecution(
                    returncode=None,
                    error="immutable benchmark harness changed before evaluation",
                    harness_unchanged=False,
                )
        except (OSError, ValueError) as exc:
            return KernelBenchmarkExecution(
                returncode=None,
                error=f"immutable benchmark harness failed preflight: {exc}",
                harness_unchanged=False,
            )
        if self._windows_launcher_path is not None:
            try:
                launcher_unchanged = _fingerprint_paths((self._windows_launcher_path,)) == self._windows_launcher_digest
            except (OSError, ValueError):
                launcher_unchanged = False
            if not launcher_unchanged:
                return KernelBenchmarkExecution(
                    returncode=None,
                    error="trusted Windows benchmark launcher changed before evaluation",
                    harness_unchanged=False,
                )
        with tempfile.TemporaryDirectory(prefix="autoctx-kernel-", dir=self._temporary_root) as temp_name:
            temp_dir = Path(temp_name)
            temp_root_identity = _process_control.filesystem_object_identity(temp_dir.lstat())
            candidate_path = temp_dir / f"candidate{self._source_suffix}"
            incumbent_path = temp_dir / f"incumbent{self._source_suffix}"
            report_dir = temp_dir / "report"
            report_dir.mkdir()
            report_root_identity = _process_control.filesystem_object_identity(report_dir.lstat())
            report_path = report_dir / "report.json"
            candidate_path.write_bytes(candidate.source_bytes)
            incumbent_path.write_bytes(incumbent.source_bytes)
            candidate_path.chmod(0o444)
            incumbent_path.chmod(0o444)

            argv = [
                arg.replace("{candidate}", str(candidate_path))
                .replace("{incumbent}", str(incumbent_path))
                .replace("{report}", str(report_path))
                .replace("{artifact_identity_version}", candidate.artifact_identity_version)
                .replace("{candidate_artifact_digest}", candidate.artifact_digest)
                .replace("{incumbent_artifact_digest}", incumbent.artifact_digest)
                .replace("{candidate_source_digest}", candidate.source_digest)
                .replace("{incumbent_source_digest}", incumbent.source_digest)
                .replace("{candidate_source_suffix}", candidate.source_suffix)
                .replace("{incumbent_source_suffix}", incumbent.source_suffix)
                .replace("{candidate_entrypoint}", candidate.entrypoint)
                .replace("{incumbent_entrypoint}", incumbent.entrypoint)
                for arg in self._command
            ]
            env = _process_control.build_benchmark_environment(temp_dir, self._environment)
            timed_out = False
            returncode: int | None = None
            error: str | None = None
            stdout_result = _process_control.BoundedOutput()
            stderr_result = _process_control.BoundedOutput()
            report_errors: list[str] = []
            quota_exceeded = threading.Event()
            report_stop = threading.Event()
            drain_threads: list[threading.Thread] = []
            report_thread: threading.Thread | None = None
            proc: subprocess.Popen[bytes] | None = None
            windows_job: _process_control.WindowsJob | None = None
            controller: _process_control.ProcessTreeController | None = None
            cleanup_errors: list[str] = []
            try:
                popen_kwargs: dict[str, Any] = {
                    "cwd": self._cwd,
                    "env": env,
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "close_fds": True,
                    "shell": False,
                }
                if sys.platform == "win32":
                    assert self._windows_launcher_path is not None
                    windows_job = _process_control.WindowsJob.create()
                    popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    popen_kwargs["stdin"] = subprocess.PIPE
                    launch_argv = [
                        str(Path(sys.executable).absolute()),
                        "-I",
                        "-S",
                        str(self._windows_launcher_path),
                        *argv,
                    ]
                else:
                    popen_kwargs["start_new_session"] = True
                    launch_argv = argv
                proc = subprocess.Popen(launch_argv, **popen_kwargs)  # noqa: S603
                controller = _process_control.ProcessTreeController(proc, windows_job)
                if windows_job is not None:
                    windows_job.assign(proc)
                assert proc.stdout is not None
                assert proc.stderr is not None

                def terminate_process() -> None:
                    assert controller is not None
                    controller.terminate()

                for stream, result in ((proc.stdout, stdout_result), (proc.stderr, stderr_result)):
                    thread = threading.Thread(
                        target=_process_control.drain_bounded,
                        args=(stream, self._max_output_bytes, result, quota_exceeded, terminate_process),
                        daemon=True,
                    )
                    thread.start()
                    drain_threads.append(thread)
                monitor = threading.Thread(
                    target=_process_control.monitor_report,
                    args=(
                        report_path,
                        self._report_limits,
                        report_root_identity,
                        report_stop,
                        report_errors,
                        quota_exceeded,
                        terminate_process,
                    ),
                    daemon=True,
                )
                monitor.start()
                report_thread = monitor
                if windows_job is not None:
                    assert proc.stdin is not None
                    written = proc.stdin.write(_WINDOWS_LAUNCH_GATE)
                    if written != len(_WINDOWS_LAUNCH_GATE):
                        raise RuntimeError("Windows benchmark launcher gate write was incomplete")
                    proc.stdin.flush()
                    proc.stdin.close()
                try:
                    returncode = proc.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = not quota_exceeded.is_set()
            except (OSError, RuntimeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                if controller is not None:
                    # A candidate can spawn a child and let its parent exit zero.
                    # Tear down the entire detached group/job after every run,
                    # before integrity checks and even after a nominal parent exit.
                    controller.terminate()
                    controller.wait_for_parent()
                if proc is not None:
                    returncode = proc.returncode
                report_stop.set()
                if report_thread is not None:
                    report_thread.join(timeout=_process_control.CLEANUP_JOIN_TIMEOUT_SECONDS)
                    if report_thread.is_alive():
                        if controller is not None:
                            controller.record_error("benchmark report monitor did not stop within cleanup timeout")
                        else:
                            cleanup_errors.append("benchmark report monitor did not stop within cleanup timeout")
                for thread in drain_threads:
                    thread.join(timeout=_process_control.CLEANUP_JOIN_TIMEOUT_SECONDS)
                if proc is not None:
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe is None:
                            continue
                        try:
                            pipe.close()
                        except (OSError, ValueError) as exc:
                            message = f"benchmark process pipe close failed: {type(exc).__name__}: {exc}"
                            if controller is not None:
                                controller.record_error(message)
                            else:
                                cleanup_errors.append(message)
                for thread in drain_threads:
                    thread.join(timeout=_process_control.CLEANUP_FORCE_KILL_TIMEOUT_SECONDS)
                if any(thread.is_alive() for thread in drain_threads):
                    if controller is not None:
                        controller.record_error("benchmark output drain did not stop within cleanup timeout")
                    else:
                        cleanup_errors.append("benchmark output drain did not stop within cleanup timeout")
                # A daemon monitor that missed its bounded join must never retain
                # authority to signal this PID or a closed/reused Windows Job.
                if controller is not None:
                    controller.disarm()
                    controller.wait_for_job_empty()
                    controller.close_job()
                    cleanup_errors.extend(controller.errors)
                elif windows_job is not None:
                    try:
                        windows_job.close()
                    except OSError as exc:
                        cleanup_errors.append(f"benchmark Windows Job handle close failed: {type(exc).__name__}: {exc}")
            if quota_exceeded.is_set() or cleanup_errors:
                timed_out = False

            # Integrity is established before timeout, exit status, output, or
            # report parsing can influence evaluator classification.
            try:
                executable_unchanged = str(Path(self._command[0]).resolve(strict=True)) == self._executable_target
            except (OSError, ValueError):
                executable_unchanged = False
            try:
                launcher_unchanged = self._windows_launcher_path is None or (
                    _fingerprint_paths((self._windows_launcher_path,)) == self._windows_launcher_digest
                )
                harness_unchanged = (
                    executable_unchanged
                    and launcher_unchanged
                    and _fingerprint_paths(self._immutable_paths) == self._harness_digest
                )
            except (OSError, ValueError):
                harness_unchanged = False
            try:
                candidate_bytes = _process_control.read_bounded_regular_file(
                    candidate_path,
                    len(candidate.source_bytes),
                    expected_parent=temp_root_identity,
                    description="candidate source",
                )
                candidate_unchanged = content_digest(candidate_bytes) == candidate.source_digest
            except (OSError, ValueError):
                candidate_unchanged = False
            try:
                incumbent_bytes = _process_control.read_bounded_regular_file(
                    incumbent_path,
                    len(incumbent.source_bytes),
                    expected_parent=temp_root_identity,
                    description="incumbent source",
                )
                incumbent_unchanged = content_digest(incumbent_bytes) == incumbent.source_digest
            except (OSError, ValueError):
                incumbent_unchanged = False

            quota_errors: list[str] = []
            if stdout_result.exceeded:
                quota_errors.append(f"benchmark stdout exceeded max_output_bytes={self._max_output_bytes}")
            if stderr_result.exceeded:
                quota_errors.append(f"benchmark stderr exceeded max_output_bytes={self._max_output_bytes}")
            quota_errors.extend(report_errors)
            quota_errors.extend(cleanup_errors)
            if quota_errors:
                error = "; ".join(([error] if error is not None else []) + quota_errors)

            payload: dict[str, Any] | None = None
            if not timed_out and error is None:
                try:
                    report_bytes = _process_control.read_bounded_regular_file(
                        report_path,
                        self._report_limits.max_bytes,
                        expected_parent=report_root_identity,
                    )
                    decoded = json.loads(
                        report_bytes.decode("utf-8"),
                        parse_constant=_reject_json_constant,
                    )
                    if not isinstance(decoded, dict):
                        raise ValueError("report root must be a JSON object")
                    payload = decoded
                except FileNotFoundError:
                    pass
                except (OSError, ValueError) as exc:
                    error = f"invalid benchmark report: {exc}"
            return KernelBenchmarkExecution(
                returncode=returncode,
                timed_out=timed_out,
                report_payload=payload,
                stdout=stdout_result.text,
                stderr=stderr_result.text,
                stdout_truncated=stdout_result.exceeded or stdout_result.read_failed,
                stderr_truncated=stderr_result.exceeded or stderr_result.read_failed,
                error=error,
                harness_unchanged=harness_unchanged,
                candidate_unchanged=candidate_unchanged,
                incumbent_unchanged=incumbent_unchanged,
            )


class KernelBenchmarkEvaluator:
    """Fail-closed consumer for the external kernel benchmark contract."""

    def __init__(self, runner: KernelBenchmarkRunner, config: KernelBenchmarkEvaluatorConfig) -> None:
        self._runner = runner
        self.config = config
        self._authority = BenchmarkAuthorityVerifier(config)

    def manifest(self) -> dict[str, Any]:
        return {"evaluator": self.config.manifest(), "runner": self._runner.manifest()}

    def evaluate(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        expected_scope_id: str | None = None,
        expected_baseline_id: str | None = None,
        expected_protocol_id: str | None = None,
    ) -> KernelBenchmarkObservation:
        try:
            execution = self._runner.run(candidate, incumbent, timeout_seconds=self.config.timeout_seconds)
        except Exception as exc:
            execution = KernelBenchmarkExecution(
                returncode=None,
                error=f"benchmark runner failed: {type(exc).__name__}: {exc}",
            )

        def reject(reason: str, feedback: str, report: KernelBenchmarkReport | None = None) -> KernelBenchmarkObservation:
            return KernelBenchmarkObservation(
                artifact_identity_version=candidate.artifact_identity_version,
                candidate_artifact_digest=candidate.artifact_digest,
                incumbent_artifact_digest=incumbent.artifact_digest,
                candidate_source_digest=candidate.source_digest,
                incumbent_source_digest=incumbent.source_digest,
                eligible=False,
                rejection_reason=reason,
                feedback=feedback[: self.config.max_feedback_chars],
                report=report,
                hardware_scope_id=report.hardware_scope_id if report is not None else None,
                baseline_id=report.baseline_id if report is not None else None,
                protocol_id=report.protocol.protocol_id if report is not None else None,
                protocol_compatibility_id=report.protocol.compatibility_id if report is not None else None,
                statistics_policy=self.config.statistics_policy,
                stdout=execution.stdout,
                stderr=execution.stderr,
                stdout_truncated=execution.stdout_truncated,
                stderr_truncated=execution.stderr_truncated,
            )

        if not execution.harness_unchanged:
            return reject("harness_modified", "The immutable benchmark harness changed during evaluation.")
        if not execution.candidate_unchanged or not execution.incumbent_unchanged:
            return reject("artifact_modified", "A candidate or incumbent artifact changed during evaluation.")
        if execution.outcome == "teardown_failed":
            return reject("teardown_failed", execution.error or "Benchmark authority teardown could not be verified.")
        if execution.outcome != "complete":
            return reject(execution.outcome, execution.error or f"Benchmark failed with {execution.outcome}.")
        if execution.timed_out:
            return reject("timeout", f"Benchmark timed out after {self.config.timeout_seconds:g}s.")
        if execution.error is not None and execution.outcome == "complete":
            return reject("contract_error", execution.error)
        if execution.returncode != 0 and execution.outcome == "complete":
            diagnostics = execution.stderr.strip() or execution.stdout.strip() or "no diagnostics"
            return reject("command_failed", f"Benchmark command exited {execution.returncode}: {diagnostics}")
        if execution.report_payload is None:
            return reject("contract_error", "Benchmark exited successfully without a valid JSON report.")
        try:
            report = KernelBenchmarkReport.model_validate(execution.report_payload)
        except ValidationError as exc:
            return reject("contract_error", f"Benchmark report failed schema validation: {exc}")

        if report.problem_id != self.config.problem_id:
            return reject("problem_mismatch", f"Expected problem {self.config.problem_id!r}, got {report.problem_id!r}.", report)
        candidate_identity_matches = (
            report.artifact_identity_version == candidate.artifact_identity_version
            and report.candidate_artifact_digest == candidate.artifact_digest
            and report.candidate_source_digest == candidate.source_digest
            and report.candidate_source_suffix == candidate.source_suffix
            and report.candidate_entrypoint == candidate.entrypoint
        )
        incumbent_identity_matches = (
            report.artifact_identity_version == incumbent.artifact_identity_version
            and report.incumbent_artifact_digest == incumbent.artifact_digest
            and report.incumbent_source_digest == incumbent.source_digest
            and report.incumbent_source_suffix == incumbent.source_suffix
            and report.incumbent_entrypoint == incumbent.entrypoint
        )
        if not candidate_identity_matches or not incumbent_identity_matches:
            return reject(
                "identity_mismatch",
                "Report source digest, suffix, entrypoint, or ABI-bound artifact identity does not match the evaluated pair.",
                report,
            )
        authority_rejection = self._authority.verify_receipt(report)
        if authority_rejection is not None:
            return reject(authority_rejection[0], authority_rejection[1], report)
        if expected_scope_id is not None and report.hardware_scope_id != expected_scope_id:
            return reject("scope_mismatch", "Hardware, toolchain, or workload fingerprint changed during the run.", report)
        if expected_baseline_id is not None and report.baseline_id != expected_baseline_id:
            return reject("baseline_mismatch", "The benchmark reference identity changed during the run.", report)
        if expected_protocol_id is not None and report.protocol.protocol_id != expected_protocol_id:
            return reject(
                "protocol_mismatch",
                "Correctness seeds, tolerances, or timing protocol changed during the run.",
                report,
            )
        if report.failure_kind in {"oom", "timeout"}:
            return reject(report.failure_kind, f"Benchmark failed with {report.failure_kind}.", report)
        if report.evaluation_status == "infrastructure_error":
            return reject("infrastructure_error", f"Benchmark infrastructure failed: {report.failure_kind}.", report)
        if not report.compile.incumbent_passed:
            return reject("incumbent_failed", "The incumbent did not compile; this measurement is not comparable.", report)
        if not report.compile.candidate_passed:
            details = report.compile.diagnostics or str(report.failure_kind or "compile")
            return reject("compile_failed", f"Candidate compilation failed: {details}", report)
        if report.correctness is None or not report.correctness.passed:
            failures = "; ".join(report.correctness.failures) if report.correctness is not None else "no trial report"
            return reject("correctness_failed", f"Candidate correctness failed: {failures}", report)
        if report.evaluation_status != "complete" or report.performance is None:
            return reject("contract_error", "A successful candidate report did not contain performance measurements.", report)
        timing_rejection = self._authority.verify_timing_comparability(report)
        if timing_rejection is not None:
            return reject(timing_rejection[0], timing_rejection[1], report)
        resource_policy = evaluate_kernel_resource_policy(
            report,
            require_telemetry=self.config.require_resource_telemetry,
            max_gpu_memory_bytes=self.config.max_gpu_memory_bytes,
        )
        if resource_policy.reason is not None:
            return reject(resource_policy.reason, resource_policy.detail, report)
        blocks = report.performance.blocks
        if len(blocks) < self.config.min_timing_blocks:
            return reject(
                "insufficient_samples",
                f"Benchmark returned {len(blocks)} timing blocks; at least {self.config.min_timing_blocks} are required.",
                report,
            )

        try:
            candidate_times = [float(block.candidate_ms) for block in blocks]
            incumbent_times = [float(block.incumbent_ms) for block in blocks]
            reference_times = [float(block.reference_ms) for block in blocks]
            candidate_median = statistics.median(candidate_times)
            incumbent_median = statistics.median(incumbent_times)
            reference_median = statistics.median(reference_times)
            speedup_incumbent = geometric_mean_ratio(incumbent_times, candidate_times)
            speedup_reference = geometric_mean_ratio(reference_times, candidate_times)
            sequential = report.protocol.sequential_testing
            alpha = sequential.per_proposal_alpha if sequential is not None else 0.05
            confidence_level = 1.0 - alpha
            seed_material = f"{report.baseline_id}:{report.hardware_scope_id}:{report.protocol.seed_commitment}"
            lcb95 = bootstrap_lcb(
                list(zip(candidate_times, incumbent_times, strict=True)),
                samples=self.config.bootstrap_samples,
                seed_material=seed_material,
                alpha=0.05,
            )
            lcb = bootstrap_lcb(
                list(zip(candidate_times, incumbent_times, strict=True)),
                samples=self.config.bootstrap_samples,
                seed_material=seed_material,
                alpha=alpha,
            )
            quartile = max(1, len(reference_times) // 4)
            first_reference = statistics.median(reference_times[:quartile])
            last_reference = statistics.median(reference_times[-quartile:])
            drift = abs(last_reference / first_reference - 1.0)
            relative_improvement = 1.0 - (1.0 / speedup_incumbent)
            feedback = (
                f"Correct on {report.correctness.tests_passed}/{report.correctness.tests_run} trials; "
                f"paired speedup {speedup_incumbent:.4f}x vs incumbent "
                f"({confidence_level:.2%} sequential lower bound {lcb:.4f}x), "
                f"{speedup_reference:.4f}x vs reference."
            )
            return KernelBenchmarkObservation(
                artifact_identity_version=candidate.artifact_identity_version,
                candidate_artifact_digest=candidate.artifact_digest,
                incumbent_artifact_digest=incumbent.artifact_digest,
                candidate_source_digest=candidate.source_digest,
                incumbent_source_digest=incumbent.source_digest,
                eligible=True,
                feedback=feedback,
                report=report,
                hardware_scope_id=report.hardware_scope_id,
                baseline_id=report.baseline_id,
                protocol_id=report.protocol.protocol_id,
                protocol_compatibility_id=report.protocol.compatibility_id,
                statistics_policy=self.config.statistics_policy,
                candidate_median_ms=candidate_median,
                incumbent_median_ms=incumbent_median,
                reference_median_ms=reference_median,
                speedup_vs_incumbent=speedup_incumbent,
                speedup_vs_reference=speedup_reference,
                speedup_lcb95=lcb95,
                speedup_lcb=lcb,
                confidence_level=confidence_level,
                all_case_no_regression_passed=(
                    all(case.passed_no_regression for case in report.performance.cases) if report.performance.cases else None
                ),
                relative_improvement=relative_improvement,
                candidate_p95_ms=percentile(candidate_times, 0.95),
                incumbent_p95_ms=percentile(incumbent_times, 0.95),
                environment_drift_ratio=drift,
                stdout=execution.stdout,
                stderr=execution.stderr,
                stdout_truncated=execution.stdout_truncated,
                stderr_truncated=execution.stderr_truncated,
            )
        except (ArithmeticError, ValueError) as exc:
            return reject("contract_error", f"Benchmark produced invalid derived statistics: {exc}", report)
