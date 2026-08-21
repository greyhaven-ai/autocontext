from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.execution.scenario_remote_package import DEFAULT_REMOTE_RUNTIME_IMAGE
from autocontext.kernel_evolution import (
    ARTIFACT_IDENTITY_VERSION,
    SCHEMA_VERSION,
    DockerGPUDeviceGrant,
    DockerKernelBenchmarkRunner,
    DockerKernelWorkerLimits,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkExecution,
    KernelBenchmarkProtocol,
    KernelBenchmarkReport,
    KernelCandidate,
    KernelCompileReport,
    KernelCorrectnessReport,
    KernelEvolutionConfig,
    KernelHardwareIdentity,
    KernelPerformanceReport,
    KernelPromotionPolicy,
    KernelResourceReport,
    KernelTimingBlock,
    content_digest,
)


def _worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, visibility_only: bool = False) -> DockerKernelBenchmarkRunner:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/docker")
    harness = tmp_path / "adapter.py"
    harness.write_text("# trusted adapter", encoding="utf-8")
    grant = DockerGPUDeviceGrant(
        device_id="MIG-GPU-deadbeef/1/0",
        isolation_kind="visibility-only" if visibility_only else "mig",
        enforced_memory_bytes=None if visibility_only else 8 * 1024**3,
    )
    return DockerKernelBenchmarkRunner(
        [
            "python",
            "{immutable_0}",
            "--candidate",
            "{candidate}",
            "--incumbent",
            "{incumbent}",
            "--report",
            "{report}",
        ],
        image=DEFAULT_REMOTE_RUNTIME_IMAGE,
        immutable_paths=[harness],
        gpu_grant=grant,
        limits=DockerKernelWorkerLimits(
            memory_mb=512,
            cpu_count=2,
            cpu_time_seconds=20,
            pids_limit=16,
            max_workspace_bytes=8 * 1024**2,
            max_workspace_inodes=64,
            max_gpu_memory_bytes=8 * 1024**3,
        ),
    )


def _report(candidate: KernelCandidate, incumbent: KernelCandidate, *, telemetry: bool = True) -> dict[str, Any]:
    hardware = KernelHardwareIdentity(
        backend="cuda",
        architecture="sm90",
        device_name="MIG H100",
        runtime="cuda-12.8",
        driver="580",
        toolchain="torch-2.8",
        workload_family_id=content_digest("family"),
        workload_fingerprint=content_digest("workload"),
        metadata={
            "device_uuid": "MIG-GPU-deadbeef/1/0",
            "device_grant": "MIG-GPU-deadbeef/1/0",
        },
    )
    protocol = KernelBenchmarkProtocol(
        correctness_trials=2,
        hidden_trials=1,
        warmup_runs=1,
        timing_blocks=5,
        calls_per_block=2,
        atol=0.01,
        rtol=0.01,
        seed_commitment=content_digest("seeds"),
    )
    resources = KernelResourceReport()
    if telemetry:
        resources = KernelResourceReport(
            candidate_artifact_digest=candidate.artifact_digest,
            incumbent_artifact_digest=incumbent.artifact_digest,
            candidate_peak_allocated_bytes=100,
            candidate_peak_reserved_bytes=120,
            incumbent_peak_allocated_bytes=90,
            incumbent_peak_reserved_bytes=110,
            candidate_peak_memory_bytes=120,
            incumbent_peak_memory_bytes=110,
            device_total_memory_bytes=8 * 1024**3,
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
        protocol=protocol,
        compile=KernelCompileReport(candidate_passed=True, incumbent_passed=True),
        correctness=KernelCorrectnessReport(
            passed=True,
            tests_run=2,
            tests_passed=2,
            hidden_tests_run=1,
            hidden_tests_passed=1,
        ),
        performance=KernelPerformanceReport(
            blocks=[KernelTimingBlock(block=index, candidate_ms=1, incumbent_ms=2, reference_ms=3) for index in range(5)]
        ),
        resources=resources,
    )
    return report.model_dump(mode="json")


class _ExecutionRunner:
    def __init__(self, execution: KernelBenchmarkExecution) -> None:
        self.execution = execution

    def manifest(self) -> dict[str, Any]:
        return {"kind": "execution-fixture"}

    def run(self, candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float) -> KernelBenchmarkExecution:
        del candidate, incumbent, timeout_seconds
        return self.execution


def test_attempted_egress_is_denied_and_candidate_harness_mounts_are_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    host_sentinel = tmp_path / "unrelated-host-sentinel"
    host_sentinel.write_text("must remain unreachable", encoding="utf-8")
    input_root = tmp_path / "input"
    report_root = tmp_path / "report"
    input_root.mkdir()
    report_root.mkdir()
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")

    command = worker._docker_command(  # noqa: SLF001 - security posture contract
        "worker-test", input_root, report_root, candidate, incumbent, 30
    )

    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--gpus") + 1] == "device=MIG-GPU-deadbeef/1/0"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert "--memory-swap" in command and "--pids-limit" in command and "--cpus" in command
    assert "cpu=20:20" in command
    assert any(value.startswith("/workspace:rw,nosuid,nodev,exec,size=8388608,nr_inodes=64") for value in command)
    mounts = [value for value in command if value.startswith("type=bind")]
    assert all("readonly" in value for value in mounts if "dst=/output" not in value)
    assert any("dst=/input,readonly" in value for value in mounts)
    assert any("dst=/benchmark/0,readonly" in value for value in mounts)
    assert str(host_sentinel) not in command
    assert not any("AWS_" in value or "TOKEN" in value or "/Users/" in value for value in command)


def test_visibility_only_gpu_grant_rejects_before_docker_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path, visibility_only=True)

    execution = worker.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=10)

    assert execution.outcome == "resource_policy_unsupported"
    assert "MIG or hardware partition" in str(execution.error)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("oom", "oom"),
        ("resource_exceeded", "resource_exceeded"),
        ("resource_policy_unsupported", "resource_policy_unsupported"),
        ("resource_identity_mismatch", "resource_identity_mismatch"),
        ("teardown_failed", "teardown_failed"),
    ],
)
def test_evaluator_preserves_distinct_worker_outcomes(outcome: Any, expected: str) -> None:
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    evaluator = KernelBenchmarkEvaluator(
        _ExecutionRunner(KernelBenchmarkExecution(returncode=None, outcome=outcome, error=expected)),
        KernelBenchmarkEvaluatorConfig(problem_id="p1"),
    )

    observation = evaluator.evaluate(candidate, incumbent)

    assert observation.rejection_reason == expected


def test_required_telemetry_rejects_and_gate_feedback_is_three_state() -> None:
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    evaluator = KernelBenchmarkEvaluator(
        _ExecutionRunner(KernelBenchmarkExecution(returncode=0, report_payload=_report(candidate, incumbent, telemetry=False))),
        KernelBenchmarkEvaluatorConfig(
            problem_id="p1",
            min_timing_blocks=5,
            bootstrap_samples=100,
            require_resource_telemetry=True,
        ),
    )
    observation = evaluator.evaluate(candidate, incumbent)
    policy = KernelPromotionPolicy(KernelEvolutionConfig(problem_id="p1", task_prompt="improve", baseline_source="baseline"))

    decision = policy.decide(observation)

    assert observation.rejection_reason == "missing_resource_telemetry"
    statuses = {gate.name: gate.status for gate in decision.gates}
    assert statuses["valid_evaluation"] == "passed"
    assert statuses["resource_telemetry"] == "failed"
    assert statuses["tail_latency"] == "not-evaluated"
    assert "resource_telemetry=failed" in decision.feedback
    assert "tail_latency=not-evaluated" in decision.feedback


def test_identity_bound_resource_telemetry_passes_and_excess_rejects() -> None:
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    execution = KernelBenchmarkExecution(returncode=0, report_payload=_report(candidate, incumbent))
    accepted = KernelBenchmarkEvaluator(
        _ExecutionRunner(execution),
        KernelBenchmarkEvaluatorConfig(
            problem_id="p1",
            min_timing_blocks=5,
            bootstrap_samples=100,
            require_resource_telemetry=True,
            max_gpu_memory_bytes=1_000,
        ),
    ).evaluate(candidate, incumbent)
    rejected = KernelBenchmarkEvaluator(
        _ExecutionRunner(execution),
        KernelBenchmarkEvaluatorConfig(
            problem_id="p1",
            min_timing_blocks=5,
            bootstrap_samples=100,
            require_resource_telemetry=True,
            max_gpu_memory_bytes=115,
        ),
    ).evaluate(candidate, incumbent)

    assert accepted.eligible
    assert accepted.report is not None
    assert accepted.report.resources.candidate_artifact_digest == candidate.artifact_digest
    assert rejected.rejection_reason == "resource_exceeded"


def test_cuda_oom_report_is_not_masked_by_missing_telemetry() -> None:
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    payload = _report(candidate, incumbent, telemetry=False)
    payload.update(
        evaluation_status="candidate_error",
        failure_kind="oom",
        correctness=None,
        performance=None,
    )
    payload["compile"] = {
        "candidate_passed": False,
        "incumbent_passed": True,
        "candidate_compile_ms": None,
        "diagnostics": "CUDA out of memory",
    }
    observation = KernelBenchmarkEvaluator(
        _ExecutionRunner(KernelBenchmarkExecution(returncode=0, report_payload=payload)),
        KernelBenchmarkEvaluatorConfig(problem_id="p1", require_resource_telemetry=True),
    ).evaluate(candidate, incumbent)

    assert observation.rejection_reason == "oom"


def test_expired_coordinator_orphan_is_reconciled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    ps_calls = 0
    removed: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal ps_calls
        del kwargs
        if argv[1:3] == ["ps", "-aq"]:
            ps_calls += 1
            return subprocess.CompletedProcess(argv, 0, stdout="orphan\n" if ps_calls == 1 else "", stderr="")
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 0, stdout="orphan\t10\n", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(worker, "_remove_container", removed.append)

    assert worker.reconcile(now=20) == 1
    assert removed == ["orphan"]


@pytest.mark.parametrize("raw_expiry", ["", "not-a-number", "nan", "inf", "0"])
def test_owned_orphan_with_invalid_expiry_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_expiry: str,
) -> None:
    worker = _worker(monkeypatch, tmp_path)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[1:3] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(argv, 0, stdout="orphan\n", stderr="")
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 0, stdout=f"orphan\t{raw_expiry}\n", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="expiry metadata"):
        worker.reconcile(now=20)


def test_non_positive_device_capacity_is_missing_resource_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    payload = _report(candidate, incumbent)
    payload["resources"]["device_total_memory_bytes"] = 0

    outcome, detail = worker._resource_telemetry_outcome(  # noqa: SLF001 - raw worker contract
        payload,
        candidate,
        incumbent,
    )

    assert outcome == "missing_resource_telemetry"
    assert "device-total" in str(detail)


class _FakePopen:
    def __init__(self, *_: Any, **__: Any) -> None:
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return 0


class _TimeoutPopen(_FakePopen):
    def __init__(self, *_: Any, **__: Any) -> None:
        super().__init__()
        self.waits = 0

    def wait(self, timeout: float | None = None) -> int:
        self.waits += 1
        if self.waits == 1:
            raise subprocess.TimeoutExpired("docker", timeout)
        self.returncode = 137
        return 137


def test_worker_timeout_is_distinct_and_verifies_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "report"
    report_root.mkdir()
    removed: list[str] = []
    verified: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", _TimeoutPopen)
    monkeypatch.setattr(worker, "_container_oom", lambda _: False)
    monkeypatch.setattr(worker, "_remove_container", removed.append)
    monkeypatch.setattr(worker, "_verify_removed", verified.append)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="timeout-worker",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=0.01,
    )

    assert execution.outcome == "timeout"
    assert execution.timed_out
    assert removed == ["timeout-worker", "timeout-worker"]
    assert verified == ["timeout-worker"]


def test_worker_oom_is_distinct_and_verifies_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "report"
    report_root.mkdir()
    removed: list[str] = []
    verified: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(worker, "_container_oom", lambda _: True)
    monkeypatch.setattr(worker, "_remove_container", removed.append)
    monkeypatch.setattr(worker, "_verify_removed", verified.append)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="oom-worker",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
    )

    assert execution.outcome == "oom"
    assert removed == ["oom-worker"]
    assert verified == ["oom-worker"]


def test_descendants_are_removed_before_coordinator_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "report"
    report_root.mkdir()
    removed: list[str] = []
    verified: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(worker, "_container_oom", lambda _: False)
    monkeypatch.setattr(worker, "_remove_container", removed.append)
    monkeypatch.setattr(worker, "_verify_removed", verified.append)

    execution = worker._execute_container(  # noqa: SLF001 - lifecycle integration seam
        ["docker", "run"],
        container_name="coordinator-failure",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
    )
    with pytest.raises(RuntimeError, match="coordinator failed"):
        assert execution.outcome == "complete"
        raise RuntimeError("coordinator failed after benchmark")

    assert removed == ["coordinator-failure"]
    assert verified == ["coordinator-failure"]


def test_cleanup_verification_failure_is_distinct_teardown_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "report"
    report_root.mkdir()
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(worker, "_container_oom", lambda _: False)
    monkeypatch.setattr(worker, "_remove_container", lambda _: None)
    monkeypatch.setattr(worker, "_verify_removed", lambda _: (_ for _ in ()).throw(RuntimeError("still alive")))

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="teardown-failure",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
    )

    assert execution.outcome == "teardown_failed"
    assert "still alive" in str(execution.error)
