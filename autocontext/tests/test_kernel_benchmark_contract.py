from __future__ import annotations

import errno
import json
import os
import select
import subprocess
import sys
import threading
import time
import venv
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from pydantic import ValidationError

from autocontext.kernel_evolution import (
    ARTIFACT_IDENTITY_VERSION,
    SCHEMA_VERSION,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkExecution,
    KernelBenchmarkProtocol,
    KernelBenchmarkReport,
    KernelCandidate,
    KernelCompileReport,
    KernelCorrectnessReport,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelHardwareIdentity,
    KernelPerformanceReport,
    KernelTimingBlock,
    _process_control,
    content_digest,
)
from autocontext.kernel_evolution import (
    ExternalKernelBenchmarkRunner as _ExternalKernelBenchmarkRunner,
)


def ExternalKernelBenchmarkRunner(*args: Any, **kwargs: Any) -> _ExternalKernelBenchmarkRunner:
    """All local-process contract fixtures are explicit trusted/unsafe runs."""

    kwargs.setdefault("trusted_unsafe", True)
    return _ExternalKernelBenchmarkRunner(*args, **kwargs)


def test_external_runner_requires_explicit_trusted_unsafe_opt_in(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text("pass", encoding="utf-8")
    with pytest.raises(PermissionError, match="trusted_unsafe=True"):
        _ExternalKernelBenchmarkRunner(
            [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
            immutable_paths=[adapter],
        )


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")


class _ProcessExitProbe:
    def __init__(self, wait_for_exit: Callable[[float], bool], close: Callable[[], None]) -> None:
        self.wait_for_exit = wait_for_exit
        self.close = close


def _open_process_exit_probe(pid: int, identity_token: str) -> _ProcessExitProbe:
    """Capture a live process identity so later PID reuse cannot mask cleanup."""

    if sys.platform == "win32":
        import ctypes

        windows_ctypes = ctypes
        kernel32 = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())

        def wait_for_windows_exit(timeout_seconds: float) -> bool:
            timeout_ms = min(0xFFFFFFFE, max(0, int(timeout_seconds * 1000)))
            result = kernel32.WaitForSingleObject(handle, timeout_ms)
            if result == 0:  # WAIT_OBJECT_0
                return True
            if result == 258:  # WAIT_TIMEOUT
                return False
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())

        def close_windows_handle() -> None:
            if not kernel32.CloseHandle(handle):
                raise windows_ctypes.WinError(windows_ctypes.get_last_error())

        return _ProcessExitProbe(wait_for_windows_exit, close_windows_handle)

    pidfd_open = getattr(os, "pidfd_open", None)
    if callable(pidfd_open):
        descriptor = pidfd_open(pid, 0)

        def wait_for_pidfd_exit(timeout_seconds: float) -> bool:
            readable, _, _ = select.select([descriptor], [], [], timeout_seconds)
            return bool(readable)

        return _ProcessExitProbe(wait_for_pidfd_exit, lambda: os.close(descriptor))

    kqueue_factory = getattr(select, "kqueue", None)
    if callable(kqueue_factory):
        process_queue = kqueue_factory()
        registration = select.kevent(
            pid,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        process_queue.control([registration], 0, 0)

        def wait_for_kqueue_exit(timeout_seconds: float) -> bool:
            return bool(process_queue.control([], 1, timeout_seconds))

        return _ProcessExitProbe(wait_for_kqueue_exit, process_queue.close)

    def same_tokenized_process_is_running() -> bool:
        completed = subprocess.run(  # noqa: S603
            ["/bin/ps", "-p", str(pid), "-o", "stat=", "-o", "command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
        if completed.returncode != 0:
            return False
        fields = completed.stdout.strip().split(maxsplit=1)
        return bool(fields) and not fields[0].startswith("Z") and len(fields) == 2 and identity_token in fields[1]

    def wait_for_tokenized_exit(timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while same_tokenized_process_is_running():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.02, remaining))
        return True

    return _ProcessExitProbe(wait_for_tokenized_exit, lambda: None)


def _report_payload(candidate: KernelCandidate, incumbent: KernelCandidate) -> dict[str, object]:
    hardware = KernelHardwareIdentity(
        backend="cuda",
        architecture="sm90",
        device_name="H100",
        runtime="12.8",
        driver="580",
        toolchain="torch-2.8/triton-3.4",
        workload_family_id=content_digest("problem-static-contract"),
        workload_fingerprint=content_digest("problem"),
    )
    report = KernelBenchmarkReport(
        schema_version=SCHEMA_VERSION,
        evaluation_status="complete",
        problem_id="p1",
        artifact_identity_version=ARTIFACT_IDENTITY_VERSION,
        candidate_artifact_digest=candidate.artifact_digest,
        incumbent_artifact_digest=incumbent.artifact_digest,
        candidate_source_digest=candidate.source_digest,
        incumbent_source_digest=incumbent.source_digest,
        candidate_source_suffix=candidate.source_suffix,
        incumbent_source_suffix=incumbent.source_suffix,
        candidate_entrypoint=candidate.entrypoint,
        incumbent_entrypoint=incumbent.entrypoint,
        baseline_id=content_digest("reference"),
        hardware=hardware,
        hardware_scope_id=hardware.scope_id,
        protocol=KernelBenchmarkProtocol(
            correctness_trials=5,
            hidden_trials=5,
            warmup_runs=3,
            timing_blocks=5,
            calls_per_block=10,
            atol=0.01,
            rtol=0.01,
            seed_commitment=content_digest("seeds"),
        ),
        compile=KernelCompileReport(candidate_passed=True, incumbent_passed=True),
        correctness=KernelCorrectnessReport(
            passed=True,
            tests_run=5,
            tests_passed=5,
            hidden_tests_run=5,
            hidden_tests_passed=5,
        ),
        performance=KernelPerformanceReport(
            blocks=[KernelTimingBlock(block=index, candidate_ms=1.0, incumbent_ms=1.1, reference_ms=2.0) for index in range(5)]
        ),
    )
    return report.model_dump(mode="json")


def test_contract_rejects_extra_keys_non_finite_and_noncontiguous_blocks() -> None:
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    payload = _report_payload(candidate, incumbent)
    payload["forged_score"] = 999
    with pytest.raises(ValidationError):
        KernelBenchmarkReport.model_validate(payload)

    payload = _report_payload(candidate, incumbent)
    payload["metadata"] = {"forged": float("inf")}
    with pytest.raises(ValidationError):
        KernelBenchmarkReport.model_validate(payload)

    payload = _report_payload(candidate, incumbent)
    payload["performance"]["blocks"][0]["candidate_ms"] = float("nan")  # type: ignore[index]
    with pytest.raises(ValidationError):
        KernelBenchmarkReport.model_validate(payload)

    payload = _report_payload(candidate, incumbent)
    payload["protocol"]["correctness_trials"] = 100  # type: ignore[index]
    payload["protocol"]["hidden_trials"] = 100  # type: ignore[index]
    with pytest.raises(ValidationError):
        KernelBenchmarkReport.model_validate(payload)

    payload = _report_payload(candidate, incumbent)
    payload["performance"]["blocks"][2]["block"] = 7  # type: ignore[index]
    with pytest.raises(ValidationError):
        KernelBenchmarkReport.model_validate(payload)

    payload = _report_payload(candidate, incumbent)
    payload["candidate_source_digest"] = content_digest("different exact bytes")
    with pytest.raises(ValidationError, match="source digest and ABI"):
        KernelBenchmarkReport.model_validate(payload)

    payload = _report_payload(candidate, incumbent)
    payload["schema_version"] = "autocontext.kernelbench-eval/v1"
    with pytest.raises(ValidationError):
        KernelBenchmarkReport.model_validate(payload)


def test_external_runner_reads_only_report_file_and_preserves_source(tmp_path: Path) -> None:
    candidate = KernelCandidate(source="```python\nnot actually valid\n```")
    incumbent = KernelCandidate(source="incumbent exact bytes\n")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(_report_payload(candidate, incumbent)), encoding="utf-8")
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import json
import sys
from pathlib import Path

candidate, incumbent, report, payload = map(Path, sys.argv[1:5])
assert candidate.read_text() == "```python\\nnot actually valid\\n```"
assert incumbent.read_text() == "incumbent exact bytes\\n"
expected = json.loads(payload.read_text())
assert sys.argv[5:] == [
    expected["artifact_identity_version"],
    expected["candidate_artifact_digest"],
    expected["incumbent_artifact_digest"],
    expected["candidate_source_digest"],
    expected["incumbent_source_digest"],
    expected["candidate_source_suffix"],
    expected["incumbent_source_suffix"],
    expected["candidate_entrypoint"],
    expected["incumbent_entrypoint"],
]
print('{"speedup": 999999, "correctness": true}')
report.write_text(payload.read_text())
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [
            sys.executable,
            str(adapter),
            "{candidate}",
            "{incumbent}",
            "{report}",
            str(payload_path),
            "{artifact_identity_version}",
            "{candidate_artifact_digest}",
            "{incumbent_artifact_digest}",
            "{candidate_source_digest}",
            "{incumbent_source_digest}",
            "{candidate_source_suffix}",
            "{incumbent_source_suffix}",
            "{candidate_entrypoint}",
            "{incumbent_entrypoint}",
        ],
        immutable_paths=[adapter, payload_path],
        temporary_root=tmp_path,
    )

    execution = runner.run(candidate, incumbent, timeout_seconds=2)

    assert execution.returncode == 0
    assert execution.report_payload == json.loads(payload_path.read_text(encoding="utf-8"))
    assert execution.harness_unchanged
    assert execution.candidate_unchanged
    assert execution.incumbent_unchanged
    assert "999999" in execution.stdout  # diagnostic only; evaluator ignores it


def test_external_runner_preserves_virtualenv_executable_path(tmp_path: Path) -> None:
    virtualenv = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(virtualenv)
    executable = virtualenv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if executable.resolve() == executable:
        pytest.skip("this platform did not create a symlinked virtualenv interpreter")

    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import sys
from pathlib import Path

candidate, incumbent, report, expected_prefix = map(Path, sys.argv[1:])
assert Path(sys.prefix).resolve() == expected_prefix.resolve()
report.write_text("{}")
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [str(executable), str(adapter), "{candidate}", "{incumbent}", "{report}", str(virtualenv)],
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert execution.returncode == 0, execution.stderr
    assert runner.manifest()["command"][0] == str(executable.absolute())
    assert runner.manifest()["executable_target"] == str(executable.resolve())


def test_external_runner_confines_temporary_environment(tmp_path: Path) -> None:
    adapter = tmp_path / "environment.py"
    adapter.write_text(
        """
import json
import os
import sys
from pathlib import Path

keys = ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "SystemRoot", "WINDIR")
Path(sys.argv[3]).write_text(json.dumps({key: os.environ.get(key) for key in keys}))
""".strip(),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    reserved = {key: str(outside / key) for key in ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "SystemRoot", "WINDIR")}
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        environment=reserved,
        temporary_root=tmp_path,
    )

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert execution.returncode == 0, execution.stderr
    assert execution.error is None
    assert execution.report_payload is not None
    confined = {
        os.path.normcase(os.path.abspath(str(execution.report_payload[key])))
        for key in ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR")
    }
    assert len(confined) == 1
    confined_root = Path(confined.pop())
    assert confined_root.parent == Path(os.path.normcase(os.path.abspath(tmp_path)))
    assert confined_root.name.startswith("autoctx-kernel-")
    if sys.platform == "win32":
        assert execution.report_payload["SystemRoot"] == os.environ.get("SystemRoot")
        assert execution.report_payload["WINDIR"] == os.environ.get("WINDIR")


def test_external_runner_detects_harness_mutation(tmp_path: Path) -> None:
    harness = tmp_path / "harness.txt"
    harness.write_text("frozen", encoding="utf-8")
    adapter = tmp_path / "mutate.py"
    adapter.write_text(
        """
import sys
from pathlib import Path

Path(sys.argv[4]).write_text("changed")
Path(sys.argv[3]).write_text("{}")
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}", str(harness)],
        immutable_paths=[adapter, harness],
        temporary_root=tmp_path,
    )

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert not execution.harness_unchanged


def test_external_runner_enforces_candidate_suffix(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import sys
from pathlib import Path

assert Path(sys.argv[1]).suffix == ".cu"
assert Path(sys.argv[2]).suffix == ".cu"
Path(sys.argv[3]).write_text("{}")
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        source_suffix=".cu",
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )

    execution = runner.run(
        KernelCandidate(source="a", source_suffix=".cu"),
        KernelCandidate(source="b", source_suffix=".cu"),
        timeout_seconds=2,
    )

    assert execution.returncode == 0, execution.stderr
    assert execution.report_payload == {}


def test_harness_fingerprint_frames_empty_root_labels(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text("from pathlib import Path\nimport sys\nPath(sys.argv[3]).write_text('{}')", encoding="utf-8")
    left = tmp_path / "left" / "same"
    right = tmp_path / "right" / "same"
    left.mkdir(parents=True)
    right.mkdir(parents=True)

    left_runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter, left],
    )
    right_runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter, right],
    )

    assert left_runner.manifest()["immutable_harness_digest"] != right_runner.manifest()["immutable_harness_digest"]


def test_external_runner_rejects_unpinned_harness(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text("pass", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable_paths"):
        ExternalKernelBenchmarkRunner([sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"])


def test_external_runner_rejects_symlinked_harness(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text("pass", encoding="utf-8")
    symlink = tmp_path / "adapter-link.py"
    _symlink_or_skip(symlink, adapter)
    with pytest.raises(ValueError, match="symlink"):
        ExternalKernelBenchmarkRunner(
            [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
            immutable_paths=[symlink],
        )

    tree = tmp_path / "tree"
    tree.mkdir()
    _symlink_or_skip(tree / "nested-link", adapter)
    with pytest.raises(ValueError, match="symlink"):
        ExternalKernelBenchmarkRunner(
            [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
            immutable_paths=[tree],
        )

    actual = tmp_path / "actual"
    actual.mkdir()
    contract = actual / "contract.txt"
    contract.write_text("contract", encoding="utf-8")
    lexical_link = tmp_path / "lexical-link"
    _symlink_or_skip(lexical_link, actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ExternalKernelBenchmarkRunner(
            [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
            immutable_paths=[lexical_link / contract.name],
        )


def test_external_runner_preflights_harness_before_execution(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    harness = tmp_path / "harness.txt"
    harness.write_text("v1", encoding="utf-8")
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n",
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter, harness],
    )
    harness.write_text("v2", encoding="utf-8")

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert not execution.harness_unchanged
    assert not marker.exists()


def _make_mutating_evaluator(
    tmp_path: Path,
    *,
    tail: str,
    timeout_seconds: float,
) -> tuple[KernelBenchmarkEvaluator, Path]:
    harness = tmp_path / "harness.txt"
    harness.write_text("frozen", encoding="utf-8")
    adapter = tmp_path / "mutate_then_fail.py"
    adapter.write_text(
        f"""
import sys
import time
from pathlib import Path

Path(sys.argv[4]).write_text("changed")
{tail}
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}", str(harness)],
        immutable_paths=[adapter, harness],
        temporary_root=tmp_path,
    )
    evaluator = KernelBenchmarkEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(problem_id="p1", timeout_seconds=timeout_seconds),
    )
    return evaluator, harness


def test_evaluator_prioritizes_postflight_harness_mutation_over_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator, harness = _make_mutating_evaluator(tmp_path, tail="time.sleep(10)", timeout_seconds=0.05)
    real_wait = subprocess.Popen.wait
    first_wait = True

    def timeout_after_mutation(process: subprocess.Popen[bytes], timeout: float | None = None) -> int:
        nonlocal first_wait
        if not first_wait:
            return real_wait(process, timeout=timeout)
        first_wait = False
        deadline = time.monotonic() + 5
        while harness.read_text(encoding="utf-8") != "changed":
            if process.poll() is not None:
                raise AssertionError("benchmark exited before mutating the harness")
            if time.monotonic() >= deadline:
                raise AssertionError("benchmark did not mutate the harness before the test deadline")
            time.sleep(0.01)
        assert timeout is not None
        raise subprocess.TimeoutExpired(process.args, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", timeout_after_mutation)

    observation = evaluator.evaluate(KernelCandidate(source="a"), KernelCandidate(source="b"))

    assert not observation.eligible
    assert observation.rejection_reason == "harness_modified"


def test_evaluator_prioritizes_postflight_harness_mutation_over_nonzero_exit(tmp_path: Path) -> None:
    evaluator, _ = _make_mutating_evaluator(tmp_path, tail="raise SystemExit(7)", timeout_seconds=2.0)

    observation = evaluator.evaluate(KernelCandidate(source="a"), KernelCandidate(source="b"))

    assert not observation.eligible
    assert observation.rejection_reason == "harness_modified"


class _DescendantFixture(NamedTuple):
    adapter: Path
    heartbeat: Path
    pid_file: Path
    release: Path
    identity_token: str


@pytest.fixture
def descendant_heartbeat_adapter(tmp_path: Path) -> _DescendantFixture:
    heartbeat = tmp_path / "heartbeat"
    pid_file = tmp_path / "child.pid"
    release = tmp_path / "release-child-outcome"
    identity_token = f"autoctx-descendant-{time.monotonic_ns()}"
    adapter = tmp_path / "descendant_adapter.py"
    adapter.write_text(
        r'''
import os
import subprocess
import sys
import time
from pathlib import Path

child_source = """
import os
import sys
import time
from pathlib import Path

heartbeat = Path(sys.argv[1])
pid_file = Path(sys.argv[2])
identity_token = sys.argv[3]
pid_file.write_text(f"{os.getpid()}\\n{identity_token}\\n")
deadline = time.monotonic() + 30
with heartbeat.open("ab", buffering=0) as stream:
    while time.monotonic() < deadline:
        stream.write(b"x")
        time.sleep(0.01)
"""
report = Path(sys.argv[3])
heartbeat = Path(sys.argv[4])
pid_file = Path(sys.argv[5])
release = Path(sys.argv[6])
mode = sys.argv[7]
identity_token = sys.argv[8]
child = subprocess.Popen(
    [sys.executable, "-c", child_source, str(heartbeat), str(pid_file), identity_token]
)
deadline = time.monotonic() + 5
while (
    not heartbeat.exists()
    or heartbeat.stat().st_size == 0
    or not pid_file.exists()
) and time.monotonic() < deadline:
    time.sleep(0.01)
assert heartbeat.exists() and heartbeat.stat().st_size > 0 and pid_file.exists()
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
assert release.exists()

if mode == "success":
    report.write_text("{}")
elif mode == "nonzero":
    raise SystemExit(7)
elif mode == "timeout":
    time.sleep(30)
elif mode == "stdout_overflow":
    while True:
        os.write(1, b"x" * 65536)
elif mode == "stderr_overflow":
    while True:
        os.write(2, b"x" * 65536)
elif mode == "report_overflow":
    temporary = report.with_name(f".{report.name}.{os.getpid()}.tmp")
    temporary.write_bytes(b"x" * 8192)
    time.sleep(30)
else:
    raise AssertionError(f"unknown mode: {mode}")
'''.strip(),
        encoding="utf-8",
    )
    return _DescendantFixture(adapter, heartbeat, pid_file, release, identity_token)


@pytest.mark.parametrize(
    ("mode", "timeout_seconds", "expected_returncode", "expected_error", "expected_timed_out"),
    [
        pytest.param("success", 5.0, 0, None, False, id="success"),
        pytest.param("nonzero", 5.0, 7, None, False, id="nonzero"),
        pytest.param("timeout", 3.0, None, None, True, id="timeout"),
        pytest.param(
            "stdout_overflow",
            5.0,
            None,
            "benchmark stdout exceeded max_output_bytes=128",
            False,
            id="stdout-overflow",
        ),
        pytest.param(
            "stderr_overflow",
            5.0,
            None,
            "benchmark stderr exceeded max_output_bytes=128",
            False,
            id="stderr-overflow",
        ),
        pytest.param(
            "report_overflow",
            5.0,
            None,
            "benchmark report directory exceeded max_report_bytes=128 during execution",
            False,
            id="report-overflow",
        ),
    ],
)
def test_external_runner_kills_descendants_for_every_terminal_outcome(
    tmp_path: Path,
    descendant_heartbeat_adapter: _DescendantFixture,
    mode: str,
    timeout_seconds: float,
    expected_returncode: int | None,
    expected_error: str | None,
    expected_timed_out: bool,
) -> None:
    fixture = descendant_heartbeat_adapter
    runner = ExternalKernelBenchmarkRunner(
        [
            sys.executable,
            str(fixture.adapter),
            "{candidate}",
            "{incumbent}",
            "{report}",
            str(fixture.heartbeat),
            str(fixture.pid_file),
            str(fixture.release),
            mode,
            fixture.identity_token,
        ],
        immutable_paths=[fixture.adapter],
        max_output_bytes=128,
        max_report_bytes=128,
        temporary_root=tmp_path,
    )

    executions: list[KernelBenchmarkExecution] = []
    runner_errors: list[Exception] = []

    def invoke_runner() -> None:
        try:
            executions.append(
                runner.run(
                    KernelCandidate(source="a"),
                    KernelCandidate(source="b"),
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception as exc:  # pragma: no cover - assertion plumbing
            runner_errors.append(exc)

    runner_thread = threading.Thread(target=invoke_runner, daemon=True)
    process_probe: _ProcessExitProbe | None = None
    runner_thread.start()
    try:
        ready_deadline = time.monotonic() + 5
        while (
            not fixture.pid_file.exists() or not fixture.heartbeat.exists() or fixture.heartbeat.stat().st_size == 0
        ) and time.monotonic() < ready_deadline:
            time.sleep(0.01)
        assert fixture.pid_file.exists()
        assert fixture.heartbeat.exists() and fixture.heartbeat.stat().st_size > 0
        pid_text, recorded_token = fixture.pid_file.read_text(encoding="utf-8").splitlines()
        assert recorded_token == fixture.identity_token
        process_probe = _open_process_exit_probe(int(pid_text), recorded_token)
        fixture.release.touch()

        runner_thread.join(timeout=timeout_seconds + 10)
        assert not runner_thread.is_alive()
        assert not runner_errors
        assert len(executions) == 1
        execution = executions[0]
        assert process_probe.wait_for_exit(2), f"descendant pid {pid_text} remained alive after {mode}"
    finally:
        fixture.release.touch(exist_ok=True)
        if runner_thread.is_alive():
            runner_thread.join(timeout=timeout_seconds + 10)
        if process_probe is not None:
            process_probe.close()

    if expected_returncode is not None:
        assert execution.returncode == expected_returncode, execution.stderr
    assert execution.error == expected_error
    assert execution.timed_out is expected_timed_out
    if mode == "success":
        assert execution.report_payload == {}
    else:
        assert execution.report_payload is None
    if mode == "stdout_overflow":
        assert execution.stdout_truncated
    if mode == "stderr_overflow":
        assert execution.stderr_truncated

    assert fixture.heartbeat.exists() and fixture.heartbeat.stat().st_size > 0
    settled_size = fixture.heartbeat.stat().st_size
    time.sleep(0.2)
    assert fixture.heartbeat.stat().st_size == settled_size


class _ReportRunner:
    def __init__(self, *, extreme: bool = False) -> None:
        self.extreme = extreme

    def manifest(self) -> dict[str, object]:
        return {"kind": "test"}

    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        del timeout_seconds
        payload = _report_payload(candidate, incumbent)
        if self.extreme:
            for block in payload["performance"]["blocks"]:  # type: ignore[index]
                block["candidate_ms"] = 1e-308
                block["incumbent_ms"] = 1e308
                block["reference_ms"] = 1e308
        return KernelBenchmarkExecution(returncode=0, report_payload=payload)


def _upgrade_execution_to_v4(execution: KernelBenchmarkExecution) -> KernelBenchmarkExecution:
    assert execution.report_payload is not None
    execution.report_payload["schema_version"] = "autocontext.kernelbench-eval/v4"
    execution.report_payload["metadata"] = {
        "measurement_design": {
            "schema_version": "autocontext.kernel-measurement-design/v1",
            "block_definition": "balanced-interleaved-paired-block/v1",
            "schedule_seed_derivation": "sha256-plan-commitment-block-schedule/v1",
            "dependence_assumption": "conditional-threshold-win-probability-lte-half/v1",
            "fixed_block_count": 5,
            "early_stopping_allowed": False,
            "order_balanced": True,
        }
    }
    return execution


class _V4ReportRunner(_ReportRunner):
    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        execution = super().run(candidate, incumbent, timeout_seconds=timeout_seconds)
        return _upgrade_execution_to_v4(execution)


class _V2ReportRunner(_ReportRunner):
    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        execution = super().run(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        execution.report_payload["schema_version"] = "autocontext.kernelbench-eval/v2"
        return execution


class _MixedSchemaReportRunner(_ReportRunner):
    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        execution = super().run(candidate, incumbent, timeout_seconds=timeout_seconds)
        return _upgrade_execution_to_v4(execution) if candidate != incumbent else execution


def test_evaluator_fails_closed_on_extreme_derived_statistics() -> None:
    evaluator = KernelBenchmarkEvaluator(
        _ReportRunner(extreme=True),
        KernelBenchmarkEvaluatorConfig(problem_id="p1", bootstrap_samples=2_000),
    )

    observation = evaluator.evaluate(KernelCandidate(source="a"), KernelCandidate(source="b"))

    assert not observation.eligible
    assert observation.rejection_reason == "contract_error"


def test_v4_report_with_bootstrap_policy_returns_typed_contract_error() -> None:
    evaluator = KernelBenchmarkEvaluator(
        _V4ReportRunner(),
        KernelBenchmarkEvaluatorConfig(problem_id="p1", bootstrap_samples=2_000),
    )

    observation = evaluator.evaluate(KernelCandidate(source="a"), KernelCandidate(source="b"))

    assert not observation.eligible
    assert observation.rejection_reason == "contract_error"
    assert "does not match the configured evidence family" in observation.feedback
    assert observation.report is None
    assert observation.statistics_policy is not None
    assert observation.statistics_policy.schema_version == "autocontext.kernel-statistics-policy/v1"
    assert observation.derived_statistics_receipt is None


def test_live_v2_report_is_reader_only_and_returns_typed_contract_error() -> None:
    evaluator = KernelBenchmarkEvaluator(
        _V2ReportRunner(),
        KernelBenchmarkEvaluatorConfig(problem_id="p1", bootstrap_samples=2_000),
    )

    observation = evaluator.evaluate(KernelCandidate(source="a"), KernelCandidate(source="b"))

    assert not observation.eligible
    assert observation.rejection_reason == "contract_error"
    assert "autocontext.kernelbench-eval/v2" in observation.feedback
    assert observation.report is None


def test_runner_persists_schema_mismatch_as_normal_rejected_attempt(tmp_path: Path) -> None:
    evaluator = KernelBenchmarkEvaluator(
        _MixedSchemaReportRunner(),
        KernelBenchmarkEvaluatorConfig(problem_id="p1", bootstrap_samples=2_000),
    )
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(problem_id="p1", task_prompt="improve", baseline_source="baseline"),
        lambda _prompt, _generation: "candidate",
        evaluator,
        tmp_path,
    )

    result = runner.run(proposals=1)

    assert len(result.attempts) == 2
    rejected = result.attempts[1]
    assert rejected.schema_version == "autocontext.kernel-lineage/v3"
    assert rejected.decision == "rejected"
    assert rejected.reason == "contract_error"
    assert rejected.observation.report is None
    assert rejected.report_digest is None


def test_evaluator_config_preserves_legacy_positional_argument_order() -> None:
    config = KernelBenchmarkEvaluatorConfig("p1", 12.5, 5, 2_000, 512, True)

    assert config.max_feedback_chars == 512
    assert config.require_resource_telemetry
    assert config.statistics_method == "paired-percentile-bootstrap/v1"
    assert config.finite_sample_improvement_margin is None


def test_bootstrap_seed_does_not_depend_on_candidate_source() -> None:
    evaluator = KernelBenchmarkEvaluator(
        _ReportRunner(),
        KernelBenchmarkEvaluatorConfig(problem_id="p1", bootstrap_samples=2_000),
    )
    incumbent = KernelCandidate(source="incumbent")

    first = evaluator.evaluate(KernelCandidate(source="candidate-a"), incumbent)
    second = evaluator.evaluate(KernelCandidate(source="candidate-b with a no-op comment"), incumbent)

    assert first.eligible and second.eligible
    assert first.speedup_lcb95 == second.speedup_lcb95


def test_external_runner_caps_zero_byte_report_entries(tmp_path: Path) -> None:
    adapter = tmp_path / "entry_flood.py"
    adapter.write_text(
        """
import sys
import time
from pathlib import Path

report_dir = Path(sys.argv[3]).parent
for index in range(10_000):
    (report_dir / f"entry-{index}").touch()
time.sleep(10)
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        max_report_entries=8,
        temporary_root=tmp_path,
    )

    started = time.monotonic()
    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=5)

    assert time.monotonic() - started < 2
    assert not execution.timed_out
    assert execution.report_payload is None
    assert execution.error == "benchmark report directory exceeded max_report_entries=8 during execution"
    assert runner.manifest()["max_report_entries"] == 8


def test_external_runner_caps_report_tree_depth(tmp_path: Path) -> None:
    adapter = tmp_path / "deep_tree.py"
    adapter.write_text(
        """
import sys
import time
from pathlib import Path

directory = Path(sys.argv[3]).parent
for index in range(8):
    directory /= str(index)
    directory.mkdir()
time.sleep(10)
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        max_report_depth=3,
        temporary_root=tmp_path,
    )

    started = time.monotonic()
    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=5)

    assert time.monotonic() - started < 2
    assert not execution.timed_out
    assert execution.report_payload is None
    assert execution.error == "benchmark report directory exceeded max_report_depth=3 during execution"
    assert runner.manifest()["max_report_depth"] == 3


def test_report_depth_scan_uses_consistent_path_identity_without_fd_scandir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "report"
    nested = root
    for index in range(4):
        nested /= str(index)
        nested.mkdir(parents=index == 0)

    real_scandir = os.scandir

    class EntryWithIncompatibleEnumerationIdentity:
        def __init__(self, entry: os.DirEntry[str]) -> None:
            self._entry = entry

        @property
        def name(self) -> str:
            return self._entry.name

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            result = list(self._entry.stat(follow_symlinks=follow_symlinks))
            result[1] += 1
            return os.stat_result(result)

    @contextmanager
    def incompatible_scandir(path: Path) -> Iterator[Iterator[EntryWithIncompatibleEnumerationIdentity]]:
        with real_scandir(path) as entries:
            yield (EntryWithIncompatibleEnumerationIdentity(entry) for entry in entries)

    monkeypatch.setattr(os, "scandir", incompatible_scandir)
    limits = _process_control.ReportLimits(max_bytes=1024, max_entries=16, max_depth=2)

    with pytest.raises(ValueError) as exc_info:
        _process_control.inspect_report_tree(
            root,
            limits,
            _process_control.filesystem_object_identity(root.lstat()),
        )

    assert str(exc_info.value) == "benchmark report directory exceeded max_report_depth=2 during execution"


@pytest.mark.parametrize(("keyword", "value"), [("max_report_entries", 0), ("max_report_depth", 0)])
def test_external_runner_rejects_nonpositive_report_tree_limits(
    tmp_path: Path,
    keyword: str,
    value: int,
) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text("pass", encoding="utf-8")

    with pytest.raises(ValueError, match="must be positive"):
        ExternalKernelBenchmarkRunner(
            [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
            immutable_paths=[adapter],
            **{keyword: value},
        )


def test_bounded_report_read_rejects_symlink(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    report = report_dir / "report.json"
    _symlink_or_skip(report, target)

    with pytest.raises(ValueError, match="regular file"):
        _process_control.read_bounded_regular_file(
            report,
            128,
            expected_parent=_process_control.filesystem_object_identity(report_dir.lstat()),
        )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are not available on this platform")
def test_bounded_report_read_rejects_special_file_without_opening_it(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    report = report_dir / "report.json"
    os.mkfifo(report)

    started = time.monotonic()
    with pytest.raises(ValueError, match="regular file"):
        _process_control.read_bounded_regular_file(
            report,
            128,
            expected_parent=_process_control.filesystem_object_identity(report_dir.lstat()),
        )
    assert time.monotonic() - started < 0.5


def test_bounded_report_read_rejects_growth_past_byte_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    report = report_dir / "report.json"
    report.write_bytes(b"{}")
    real_read = os.read
    grew = False

    def read_then_grow(descriptor: int, size: int) -> bytes:
        nonlocal grew
        chunk = real_read(descriptor, size)
        if not grew:
            grew = True
            with report.open("ab") as stream:
                stream.write(b"x")
        return chunk

    monkeypatch.setattr(_process_control.os, "read", read_then_grow)

    with pytest.raises(ValueError, match="report exceeds 2 bytes"):
        _process_control.read_bounded_regular_file(
            report,
            2,
            expected_parent=_process_control.filesystem_object_identity(report_dir.lstat()),
        )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows denies replacement of an open report file")
def test_bounded_report_read_rejects_path_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    report = report_dir / "report.json"
    replacement = report_dir / "replacement.json"
    report.write_bytes(b"{}")
    replacement.write_bytes(b"[]")
    real_read = os.read
    replaced = False

    def read_then_replace(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = real_read(descriptor, size)
        if not replaced:
            replaced = True
            replacement.replace(report)
        return chunk

    monkeypatch.setattr(_process_control.os, "read", read_then_replace)

    with pytest.raises(ValueError, match="report path changed while reading"):
        _process_control.read_bounded_regular_file(
            report,
            128,
            expected_parent=_process_control.filesystem_object_identity(report_dir.lstat()),
        )


def test_external_runner_bounds_postflight_source_verification(tmp_path: Path) -> None:
    adapter = tmp_path / "grow_source.py"
    adapter.write_text(
        """
import sys
from pathlib import Path

candidate = Path(sys.argv[1])
candidate.chmod(0o644)
with candidate.open("ab") as stream:
    stream.write(b"x" * 1_000_000)
Path(sys.argv[3]).write_text("{}")
""".strip(),
        encoding="utf-8",
    )
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert execution.returncode == 0
    assert not execution.candidate_unchanged
    assert execution.incumbent_unchanged


def test_external_runner_surfaces_report_monitor_cleanup_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "from pathlib import Path\nimport sys\nPath(sys.argv[3]).write_text('{}')",
        encoding="utf-8",
    )
    release_monitor = threading.Event()
    late_callback_returned = threading.Event()
    kill_calls: list[int] = []
    real_kill_process_group = _process_control.kill_process_group

    def stalled_monitor(*args: object) -> None:
        release_monitor.wait()
        terminate = args[-1]
        assert callable(terminate)
        terminate()
        late_callback_returned.set()

    def recording_kill_process_group(
        proc: subprocess.Popen[bytes],
        windows_job: _process_control.WindowsJob | None = None,
    ) -> None:
        kill_calls.append(proc.pid)
        real_kill_process_group(proc, windows_job)

    monkeypatch.setattr(_process_control, "monitor_report", stalled_monitor)
    monkeypatch.setattr(_process_control, "kill_process_group", recording_kill_process_group)
    monkeypatch.setattr(_process_control, "CLEANUP_JOIN_TIMEOUT_SECONDS", 0.01)
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )

    try:
        execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)
        kill_count_after_cleanup = len(kill_calls)
    finally:
        release_monitor.set()

    assert late_callback_returned.wait(1)
    assert len(kill_calls) == kill_count_after_cleanup
    assert not execution.timed_out
    assert execution.report_payload is None
    assert execution.error == "benchmark report monitor did not stop within cleanup timeout"


def test_external_runner_surfaces_process_tree_termination_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "from pathlib import Path\nimport sys\nPath(sys.argv[3]).write_text('{}')",
        encoding="utf-8",
    )
    real_kill_process_group = _process_control.kill_process_group

    def terminate_then_fail(
        proc: subprocess.Popen[bytes],
        windows_job: _process_control.WindowsJob | None = None,
    ) -> None:
        real_kill_process_group(proc, windows_job)
        raise PermissionError(errno.EACCES, "injected termination failure")

    monkeypatch.setattr(_process_control, "kill_process_group", terminate_then_fail)
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert not execution.timed_out
    assert execution.report_payload is None
    assert execution.error is not None
    assert execution.error.startswith("benchmark process tree termination failed: PermissionError:")


@pytest.mark.parametrize(
    ("gate_input", "expected_returncode", "expected_start"),
    [(b"", 125, False), (b"\x00", 125, False), (b"\x01", 0, True)],
)
def test_windows_job_launcher_requires_go_signal(
    tmp_path: Path,
    gate_input: bytes,
    expected_returncode: int,
    expected_start: bool,
) -> None:
    marker = tmp_path / "executed"
    target = tmp_path / "target.py"
    target.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')",
        encoding="utf-8",
    )
    launcher = Path(_process_control.__file__).with_name("_windows_job_launcher.py")

    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(launcher), sys.executable, str(target)],
        input=gate_input,
        capture_output=True,
        check=False,
        timeout=2,
    )

    assert completed.returncode == expected_returncode
    assert marker.exists() is expected_start


@pytest.mark.skipif(sys.platform != "win32", reason="requires the native Windows launcher path")
def test_external_runner_uses_site_isolated_windows_launcher_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "from pathlib import Path\nimport sys\nPath(sys.argv[3]).write_text('{}')",
        encoding="utf-8",
    )
    captured_argv: list[list[str]] = []
    real_popen = subprocess.Popen

    def recording_popen(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        captured_argv.append(list(argv))
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert execution.returncode == 0, execution.stderr
    assert execution.error is None
    assert len(captured_argv) == 1
    launch_argv = captured_argv[0]
    run_root = Path(launch_argv[6]).parent
    launcher = Path(_process_control.__file__).with_name("_windows_job_launcher.py").resolve()
    assert launch_argv == [
        str(Path(sys.executable).absolute()),
        "-I",
        "-S",
        str(launcher),
        str(runner.manifest()["command"][0]),
        str(adapter),
        str(run_root / "candidate.py"),
        str(run_root / "incumbent.py"),
        str(run_root / "report" / "report.json"),
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="requires a native Windows Job Object")
def test_external_runner_never_starts_target_when_windows_job_assignment_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "executed"
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')",
        encoding="utf-8",
    )

    def refuse_assignment(
        job: _process_control.WindowsJob,
        proc: subprocess.Popen[bytes],
    ) -> None:
        del job, proc
        raise PermissionError(errno.EACCES, "injected Job assignment failure")

    monkeypatch.setattr(_process_control.WindowsJob, "assign", refuse_assignment)
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )
    evaluator = KernelBenchmarkEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(problem_id="p1", timeout_seconds=2),
    )

    observation = evaluator.evaluate(KernelCandidate(source="a"), KernelCandidate(source="b"))

    assert not marker.exists()
    assert not observation.eligible
    assert observation.rejection_reason == "contract_error"
    assert "injected Job assignment failure" in observation.feedback


@pytest.mark.skipif(sys.platform != "win32", reason="requires a native Windows Job Object")
@pytest.mark.parametrize(
    ("method_name", "error_prefix"),
    [
        ("wait_until_empty", "benchmark Windows Job status query failed: OSError:"),
        ("close", "benchmark Windows Job handle close failed: OSError:"),
    ],
)
def test_external_runner_surfaces_windows_job_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    error_prefix: str,
) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "from pathlib import Path\nimport sys\nPath(sys.argv[3]).write_text('{}')",
        encoding="utf-8",
    )
    real_method = getattr(_process_control.WindowsJob, method_name)

    def run_then_fail(job: _process_control.WindowsJob, *args: object) -> None:
        real_method(job, *args)
        raise OSError(errno.EIO, f"injected {method_name} failure")

    monkeypatch.setattr(_process_control.WindowsJob, method_name, run_then_fail)
    runner = ExternalKernelBenchmarkRunner(
        [sys.executable, str(adapter), "{candidate}", "{incumbent}", "{report}"],
        immutable_paths=[adapter],
        temporary_root=tmp_path,
    )

    execution = runner.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=2)

    assert not execution.timed_out
    assert execution.report_payload is None
    assert execution.error is not None
    assert execution.error.startswith(error_prefix)
