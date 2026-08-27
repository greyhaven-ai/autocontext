from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
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
from autocontext.kernel_evolution import docker_supervisor as docker_supervisor_module
from autocontext.kernel_evolution import docker_watchdog as docker_watchdog_module
from autocontext.kernel_evolution import docker_worker as docker_worker_module
from autocontext.kernel_evolution import docker_worker_runtime as docker_worker_runtime_module
from autocontext.kernel_evolution import gpu_attestation as gpu_attestation_module
from autocontext.kernel_evolution.docker_worker import (
    DockerGPUDeviceAttestation,
    NvidiaSMIGPUDeviceAttestor,
)


class _StaticGPUAttestor:
    attestor_id = "test-host-attestor-v1"

    def __init__(self, *, capacity: int = 8 * 1024**3, device_id: str = "MIG-GPU-deadbeef/1/0") -> None:
        self.capacity = capacity
        self.device_id = device_id

    def manifest(self) -> dict[str, Any]:
        return {"attestor_id": self.attestor_id, "kind": "test-static"}

    def attest(self, grant: DockerGPUDeviceGrant) -> DockerGPUDeviceAttestation:
        del grant
        return DockerGPUDeviceAttestation(
            device_id=self.device_id,
            isolation_kind="mig",
            enforced_memory_bytes=self.capacity,
            attestor_id=self.attestor_id,
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
        gpu_attestor=_StaticGPUAttestor(),
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
            "device_isolation_kind": "mig",
            "device_enforced_memory_bytes": str(8 * 1024**3),
            "device_attestor_id": "test-host-attestor-v1",
            "device_attestation_digest": DockerGPUDeviceAttestation(
                device_id="MIG-GPU-deadbeef/1/0",
                isolation_kind="mig",
                enforced_memory_bytes=8 * 1024**3,
                attestor_id="test-host-attestor-v1",
            ).digest,
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
    attestation, error = worker._attest_gpu()  # noqa: SLF001 - security posture contract
    assert error is None and attestation is not None

    command = worker._docker_command(  # noqa: SLF001 - security posture contract
        "worker-test", input_root, candidate, incumbent, attestation, time.time() + 30
    )

    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--log-driver") + 1] == "none"
    assert command[command.index("--gpus") + 1] == "device=MIG-GPU-deadbeef/1/0"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert "--memory-swap" in command and "--pids-limit" in command and "--cpus" in command
    assert "cpu=20:20" in command
    assert any(value.startswith("/workspace:rw,nosuid,nodev,exec,size=8388608,nr_inodes=64") for value in command)
    assert any(value.startswith("/output:rw,noexec,nosuid,nodev,size=2000000,nr_inodes=16") for value in command)
    assert not any(value.startswith("fsize=") for value in command)
    mounts = [value for value in command if value.startswith("type=bind")]
    assert all("readonly" in value for value in mounts)
    assert not any("dst=/output" in value for value in mounts)
    assert any("dst=/input,readonly" in value for value in mounts)
    assert any("dst=/benchmark/0,readonly" in value for value in mounts)
    assert any("dst=/autocontext-docker-supervisor.py,readonly" in value for value in mounts)
    assert "--interactive" in command
    assert str(host_sentinel) not in command
    assert not any("AWS_" in value or "TOKEN" in value for value in command)
    assert "AUTOCONTEXT_GPU_ISOLATION_KIND=mig" in command
    assert f"AUTOCONTEXT_GPU_ENFORCED_MEMORY_BYTES={8 * 1024**3}" in command
    assert f"AUTOCONTEXT_GPU_ATTESTOR_ID={attestation.attestor_id}" in command
    assert f"AUTOCONTEXT_GPU_ATTESTATION_DIGEST={attestation.digest}" in command


def test_visibility_only_gpu_grant_rejects_before_docker_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path, visibility_only=True)

    execution = worker.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=10)

    assert execution.outcome == "resource_policy_unsupported"
    assert "MIG or hardware partition" in str(execution.error)


def test_plain_gpu_index_cannot_claim_mig_isolation() -> None:
    with pytest.raises(ValueError, match="explicit MIG UUID"):
        DockerGPUDeviceGrant(device_id="0", isolation_kind="mig", enforced_memory_bytes=8 * 1024**3)


def test_nvidia_attestor_cross_checks_topology_and_nvml_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_id = "MIG-GPU-deadbeef/1/0"
    capacity = 8 * 1024**3
    monkeypatch.setattr(gpu_attestation_module.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    attestor = NvidiaSMIGPUDeviceAttestor("nvidia-smi")
    monkeypatch.setattr(attestor, "_run_nvml_capacity", lambda *_: capacity)
    monkeypatch.setattr(
        attestor,
        "_run",
        lambda _: subprocess.CompletedProcess(
            [],
            0,
            stdout=f"GPU 0: H100 (UUID: GPU-parent)\n  MIG 1g.10gb Device 0: (UUID: {device_id})\n",
            stderr="",
        ),
    )

    attestation = attestor.attest(
        DockerGPUDeviceGrant(device_id=device_id, isolation_kind="mig", enforced_memory_bytes=capacity)
    )

    assert attestation.device_id == device_id
    assert attestation.enforced_memory_bytes == capacity
    assert attestor.manifest() == {
        "attestor_id": "nvidia-smi-nvml-mig-v1",
        "nvidia_smi_binary": "/usr/bin/nvidia-smi",
        "nvml_library": "libnvidia-ml.so.1",
        "timeout_seconds": 10.0,
    }


def test_nvml_capacity_helper_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_attestation_module.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    attestor = NvidiaSMIGPUDeviceAttestor("nvidia-smi", timeout_seconds=0.25)

    def timeout(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired("nvml-helper", 0.25)

    monkeypatch.setattr(gpu_attestation_module.subprocess, "run", timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        attestor._run_nvml_capacity("MIG-GPU-deadbeef/1/0")  # noqa: SLF001


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    [
        (1, "", "NVML failed"),
        (0, "not-a-number\n", ""),
        (0, "0\n", ""),
        (0, "1\nextra\n", ""),
    ],
)
def test_nvml_capacity_helper_rejects_failure_and_malformed_output(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    monkeypatch.setattr(gpu_attestation_module.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    attestor = NvidiaSMIGPUDeviceAttestor("nvidia-smi")
    monkeypatch.setattr(
        gpu_attestation_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode, stdout=stdout, stderr=stderr),
    )

    with pytest.raises(RuntimeError, match="NVML MIG capacity helper failed"):
        attestor._run_nvml_capacity("MIG-GPU-deadbeef/1/0")  # noqa: SLF001


def test_attested_capacity_must_exactly_match_configured_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    worker.gpu_attestor = _StaticGPUAttestor(capacity=4 * 1024**3)

    execution = worker.run(KernelCandidate(source="a"), KernelCandidate(source="b"), timeout_seconds=10)

    assert execution.outcome == "resource_policy_unsupported"
    assert "capacity does not match" in str(execution.error)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("timeout", "timeout"),
        ("oom", "oom"),
        ("resource_exceeded", "resource_exceeded"),
        ("resource_policy_unsupported", "resource_policy_unsupported"),
        ("missing_resource_telemetry", "missing_resource_telemetry"),
        ("resource_identity_mismatch", "resource_identity_mismatch"),
        ("protocol_corruption", "protocol_corruption"),
        ("evaluator_crashed", "evaluator_crashed"),
        ("candidate_crashed", "candidate_crashed"),
        ("teardown_failed", "teardown_failed"),
    ],
)
def test_evaluator_preserves_distinct_worker_outcomes(outcome: Any, expected: str) -> None:
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    forged_payload = _report(candidate, incumbent)
    forged_payload["problem_id"] = "candidate-controlled-forged-problem"
    evaluator = KernelBenchmarkEvaluator(
        _ExecutionRunner(
            KernelBenchmarkExecution(
                returncode=None,
                timed_out=True,
                outcome=outcome,
                error=expected,
                report_payload=forged_payload,
            )
        ),
        KernelBenchmarkEvaluatorConfig(problem_id="p1"),
    )

    observation = evaluator.evaluate(candidate, incumbent)

    assert observation.rejection_reason == expected
    assert observation.feedback == expected
    assert observation.report is None


def test_required_telemetry_rejects_and_gate_feedback_is_three_state() -> None:
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    evaluator = KernelBenchmarkEvaluator(
        _ExecutionRunner(KernelBenchmarkExecution(returncode=0, report_payload=_report(candidate, incumbent, telemetry=False))),
        KernelBenchmarkEvaluatorConfig(
            problem_id="p1",
            min_timing_blocks=5,
            bootstrap_samples=2_000,
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
            bootstrap_samples=2_000,
            require_resource_telemetry=True,
            max_gpu_memory_bytes=1_000,
        ),
    ).evaluate(candidate, incumbent)
    rejected = KernelBenchmarkEvaluator(
        _ExecutionRunner(execution),
        KernelBenchmarkEvaluatorConfig(
            problem_id="p1",
            min_timing_blocks=5,
            bootstrap_samples=2_000,
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


def test_worker_preserves_adapter_oom_before_telemetry_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    payload = _report(candidate, incumbent, telemetry=False)
    payload["failure_kind"] = "oom"
    monkeypatch.setattr(worker, "reconcile", lambda: 0)
    monkeypatch.setattr(worker, "_image_available", lambda: True)
    monkeypatch.setattr(
        worker,
        "_execute_container",
        lambda *args, **kwargs: KernelBenchmarkExecution(returncode=1, report_payload=payload),
    )

    execution = worker.run(candidate, incumbent, timeout_seconds=1)

    assert execution.outcome == "oom"
    assert "out-of-memory" in str(execution.error)


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


def test_reconcile_tolerates_container_removed_between_list_and_inspect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    ps_calls = 0

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal ps_calls
        del kwargs
        if argv[1:3] == ["ps", "-aq"]:
            ps_calls += 1
            return subprocess.CompletedProcess(argv, 0, stdout="raced\n" if ps_calls == 1 else "", stderr="")
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Error: No such object: raced")
        raise AssertionError(argv)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert worker.reconcile(now=20) == 0


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
    attestation, error = worker._attest_gpu()  # noqa: SLF001
    assert error is None and attestation is not None

    outcome, detail = worker._resource_telemetry_outcome(  # noqa: SLF001 - raw worker contract
        payload,
        candidate,
        incumbent,
        attestation,
    )

    assert outcome == "missing_resource_telemetry"
    assert "device-total" in str(detail)


def test_report_attestor_identity_must_match_host_attestation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    candidate = KernelCandidate(source="candidate")
    incumbent = KernelCandidate(source="incumbent")
    payload = _report(candidate, incumbent)
    payload["hardware"]["metadata"]["device_attestor_id"] = "forged-attestor"
    attestation, error = worker._attest_gpu()  # noqa: SLF001
    assert error is None and attestation is not None

    outcome, detail = worker._resource_telemetry_outcome(  # noqa: SLF001 - raw worker contract
        payload,
        candidate,
        incumbent,
        attestation,
    )

    assert outcome == "resource_identity_mismatch"
    assert "explicit GPU grant" in str(detail)


def test_hostile_report_json_depth_and_entry_limits_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "bounded-report"
    report_root.mkdir()
    identity = docker_worker_module._process_control.filesystem_object_identity(  # noqa: SLF001
        report_root.lstat()
    )
    report = report_root / "report.json"

    report.write_text("[" * 100_000 + "0" + "]" * 100_000, encoding="utf-8")
    assert worker._read_report(report, identity) is None  # noqa: SLF001

    report.write_text(
        "{" + ",".join(f'\"key-{index}\":0' for index in range(worker.limits.max_report_entries + 1)) + "}",
        encoding="utf-8",
    )
    assert worker._read_report(report, identity) is None  # noqa: SLF001


def test_supervisor_status_collector_handles_all_frame_splits_and_fake_mac() -> None:
    secret = bytes(range(32))
    completion = docker_supervisor_module.DockerSupervisorCompletion(
        adapter_returncode=-signal.SIGTERM,
        completed_at_ns=time.time_ns(),
        report_size=17,
        report_sha256="a" * 64,
    )
    frame = docker_supervisor_module.encode_completion_frame(secret, completion)
    forged = docker_supervisor_module.encode_completion_frame(b"x" * 32, completion)

    for split in range(len(frame) + 1):
        collector = docker_supervisor_module.DockerSupervisorStatusCollector(secret, max_report_bytes=100)
        collector.feed(b"candidate output\n" + forged + frame[:split])
        collector.feed(frame[split:])
        assert collector.completion == completion
        assert collector.authenticated_frame == frame


def test_supervisor_status_collector_stays_bounded_under_unterminated_garbage() -> None:
    secret = b"s" * 32
    completion = docker_supervisor_module.DockerSupervisorCompletion(
        adapter_returncode=0,
        completed_at_ns=time.time_ns(),
        report_size=None,
        report_sha256=None,
    )
    frame = docker_supervisor_module.encode_completion_frame(secret, completion)
    collector = docker_supervisor_module.DockerSupervisorStatusCollector(secret, max_report_bytes=100)

    collector.feed(b"\n")
    collector.feed(b"z" * 10_000_000)
    assert collector.buffered_bytes <= docker_supervisor_module.MAX_SUPERVISOR_WIRE_BYTES
    collector.feed(docker_supervisor_module.SUPERVISOR_STATUS_PREFIX + b"z" * 10_000_000)
    assert collector.buffered_bytes <= docker_supervisor_module.MAX_SUPERVISOR_WIRE_BYTES
    collector.feed(b"\ninvalid oversized frame\n" + frame)

    assert collector.completion == completion
    assert collector.buffered_bytes <= docker_supervisor_module.MAX_SUPERVISOR_WIRE_BYTES


def test_supervisor_completion_rejects_impossible_negative_status() -> None:
    secret = b"s" * 32
    completion = docker_supervisor_module.DockerSupervisorCompletion(
        adapter_returncode=-signal.NSIG,
        completed_at_ns=time.time_ns(),
        report_size=None,
        report_sha256=None,
    )

    assert (
        docker_supervisor_module.decode_completion_frame(
            secret,
            docker_supervisor_module.encode_completion_frame(secret, completion),
            max_report_bytes=100,
        )
        is None
    )

class _ProtocolInput:
    def __init__(self, process: _FakePopen) -> None:
        self.process = process
        self.closed = False

    def write(self, payload: bytes) -> int:
        if payload.startswith(docker_supervisor_module.SUPERVISOR_START_PREFIX):
            self.process.record("START")
            secret_hex = payload[len(docker_supervisor_module.SUPERVISOR_START_PREFIX) :].strip()
            self.process.secret = bytes.fromhex(secret_hex.decode("ascii"))
            self.process.emit_completion()
        elif payload.startswith(docker_supervisor_module.SUPERVISOR_ACK_PREFIX):
            self.process.record("ACK")
            assert self.process.secret is not None and self.process.completion is not None
            assert payload == docker_supervisor_module.encode_ack(self.process.secret, self.process.completion)
            self.process.returncode = docker_supervisor_module.normalized_adapter_exit_code(
                self.process.completion.adapter_returncode
            )
            self.process.close_output()
        else:
            raise AssertionError("unexpected supervisor stdin frame")
        return len(payload)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    adapter_returncode = 0
    candidate_stdout = b""
    report_payload: bytes | None = None
    events: list[str] | None = None

    def __init__(self, *_: Any, **__: Any) -> None:
        stdout_read, self._stdout_write = os.pipe()
        stderr_read, self._stderr_write = os.pipe()
        self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
        self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
        self.stdin = _ProtocolInput(self)
        self.returncode: int | None = None
        self.secret: bytes | None = None
        self.completion: docker_supervisor_module.DockerSupervisorCompletion | None = None
        self._output_closed = False
        self.record("start")

    def record(self, event: str) -> None:
        if self.events is not None:
            self.events.append(event)

    def emit_completion(self) -> None:
        assert self.secret is not None
        report_sha256 = hashlib.sha256(self.report_payload).hexdigest() if self.report_payload is not None else None
        self.completion = docker_supervisor_module.DockerSupervisorCompletion(
            adapter_returncode=self.adapter_returncode,
            completed_at_ns=self.completed_at_ns(),
            report_size=len(self.report_payload) if self.report_payload is not None else None,
            report_sha256=report_sha256,
        )
        self.record("completion")
        os.write(self._stdout_write, self.candidate_stdout)
        os.write(
            self._stdout_write,
            b"\n" + docker_supervisor_module.encode_completion_frame(self.secret, self.completion),
        )

    def completed_at_ns(self) -> int:
        return time.time_ns() - 1_000_000

    def close_output(self) -> None:
        if not self._output_closed:
            os.close(self._stdout_write)
            os.close(self._stderr_write)
            self._output_closed = True

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("docker", timeout or 0.0)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


class _TimeoutPopen(_FakePopen):
    def emit_completion(self) -> None:
        return None


class _DeadlineRemovedPopen(_TimeoutPopen):
    pass


class _LateSuccessPopen(_FakePopen):
    def completed_at_ns(self) -> int:
        return time.time_ns() + 10_000_000_000


def _disable_real_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_worker_runtime_module, "launch_deadline_watchdog", lambda *args: None)
    monkeypatch.setattr(DockerKernelBenchmarkRunner, "_create_container", lambda *args, **kwargs: None)

    def terminate(process: _FakePopen, *, description: str) -> None:
        del description
        process.returncode = 137
        process.close_output()

    monkeypatch.setattr(docker_worker_runtime_module, "terminate_process_group", terminate)


def test_supervisor_lifecycle_copies_and_verifies_before_ack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "protocol-report"
    report_root.mkdir()
    report_path = report_root / "report.json"
    report_payload = b'{"ok":true}'
    events: list[str] = []

    class _ReportPopen(_FakePopen):
        adapter_returncode = 7
        candidate_stdout = b"candidate output"

    _ReportPopen.report_payload = report_payload
    _ReportPopen.events = events
    processes: list[_ReportPopen] = []

    def popen(*args: Any, **kwargs: Any) -> _ReportPopen:
        process = _ReportPopen(*args, **kwargs)
        processes.append(process)
        return process

    def copy_report(
        container_name: str,
        destination: Path,
        completion: docker_supervisor_module.DockerSupervisorCompletion,
        *,
        timeout_seconds: float,
    ) -> None:
        assert container_name == "ordered-worker"
        assert completion.report_size == len(report_payload)
        assert timeout_seconds > 0
        events.append("copy")
        destination.write_bytes(report_payload)

    original_verify = worker._verify_copied_report  # noqa: SLF001

    def verify_report(
        destination: Path,
        root_identity: tuple[int, int, int],
        completion: docker_supervisor_module.DockerSupervisorCompletion,
    ) -> None:
        events.append("verify")
        original_verify(destination, root_identity, completion)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(worker, "_create_container", lambda *args, **kwargs: events.append("create"))
    monkeypatch.setattr(worker, "_copy_report", copy_report)
    monkeypatch.setattr(worker, "_verify_copied_report", verify_report)
    monkeypatch.setattr(
        worker,
        "_container_oom",
        lambda *args, **kwargs: events.append("inspect") or False,
    )
    monkeypatch.setattr(worker, "_remove_container", lambda _: events.append("rm"))
    monkeypatch.setattr(worker, "_verify_removed", lambda _: events.append("verify-removed"))
    monkeypatch.setattr(
        docker_worker_runtime_module,
        "launch_deadline_watchdog",
        lambda *args: events.append("watchdog") or None,
    )
    monkeypatch.setattr(docker_worker_runtime_module, "terminate_process_group", lambda *args, **kwargs: None)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="ordered-worker",
        report_path=report_path,
        report_root_identity=docker_worker_module._process_control.filesystem_object_identity(  # noqa: SLF001
            report_root.lstat()
        ),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
    )

    assert execution.returncode == 7
    assert execution.report_payload == {"ok": True}
    assert execution.stdout == "candidate output"
    assert docker_supervisor_module.SUPERVISOR_PROTOCOL_VERSION not in execution.stdout
    assert events == [
        "create",
        "watchdog",
        "start",
        "START",
        "completion",
        "copy",
        "verify",
        "inspect",
        "ACK",
        "rm",
        "verify-removed",
    ]
    assert len(processes) == 1 and processes[0].secret is not None


def test_live_tmpfs_report_extraction_uses_bounded_exec_not_docker_cp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["argv"] = argv
        captured["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(argv, 0, stdout=b'{"ok":true}', stderr=b"")

    monkeypatch.setattr(docker_worker_runtime_module.subprocess, "run", run)
    report_path = tmp_path / "exec-report.json"
    docker_worker_runtime_module.copy_live_tmpfs_report(
        docker_binary="/usr/bin/docker",
        container_name="live-worker",
        report_path=report_path,
        container_python="/usr/local/bin/python",
        max_report_bytes=64,
        timeout_seconds=3.0,
    )

    argv = captured["argv"]
    assert argv[:2] == ["/usr/bin/docker", "exec"]
    assert "cp" not in argv
    assert argv[argv.index("live-worker") + 1] == "/usr/local/bin/python"
    assert "-I" in argv and "-B" in argv and "-S" in argv
    assert captured["timeout"] == 3.0
    assert report_path.read_bytes() == b'{"ok":true}'


@pytest.mark.parametrize(
    ("result", "expected_exception"),
    [
        (subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"reader failed"), RuntimeError),
        (subprocess.CompletedProcess([], 0, stdout=b"x" * 65, stderr=b""), RuntimeError),
    ],
)
def test_live_tmpfs_report_extraction_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: subprocess.CompletedProcess[bytes],
    expected_exception: type[Exception],
) -> None:
    monkeypatch.setattr(docker_worker_runtime_module.subprocess, "run", lambda *args, **kwargs: result)
    report_path = tmp_path / "failed-report.json"

    with pytest.raises(expected_exception):
        docker_worker_runtime_module.copy_live_tmpfs_report(
            docker_binary="/usr/bin/docker",
            container_name="live-worker",
            report_path=report_path,
            container_python="/usr/local/bin/python",
            max_report_bytes=64,
            timeout_seconds=3.0,
        )

    assert not report_path.exists()


def test_live_tmpfs_report_extraction_propagates_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def timeout(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        raise subprocess.TimeoutExpired("docker exec", 0.25)

    monkeypatch.setattr(docker_worker_runtime_module.subprocess, "run", timeout)
    report_path = tmp_path / "timed-out-report.json"

    with pytest.raises(subprocess.TimeoutExpired):
        docker_worker_runtime_module.copy_live_tmpfs_report(
            docker_binary="/usr/bin/docker",
            container_name="live-worker",
            report_path=report_path,
            container_python="/usr/local/bin/python",
            max_report_bytes=64,
            timeout_seconds=0.25,
        )

    assert not report_path.exists()


@pytest.mark.parametrize(("extra_bytes", "expected_outcome"), [(0, "complete"), (1, "resource_exceeded")])
def test_supervisor_frame_does_not_consume_candidate_stdout_quota(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra_bytes: int,
    expected_outcome: str,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / f"stdout-{extra_bytes}"
    report_root.mkdir()

    class _OutputPopen(_FakePopen):
        pass

    events: list[str] = []
    _OutputPopen.events = events
    _OutputPopen.candidate_stdout = b"x" * (worker.limits.max_output_bytes + extra_bytes)
    processes: list[_OutputPopen] = []

    def popen(*args: Any, **kwargs: Any) -> _OutputPopen:
        process = _OutputPopen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_container_oom", lambda *args, **kwargs: False)
    monkeypatch.setattr(worker, "_remove_container", lambda _: None)
    monkeypatch.setattr(worker, "_verify_removed", lambda _: None)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name=f"stdout-worker-{extra_bytes}",
        report_path=report_root / "report.json",
        report_root_identity=docker_worker_module._process_control.filesystem_object_identity(  # noqa: SLF001
            report_root.lstat()
        ),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
    )

    assert execution.outcome == expected_outcome
    assert execution.stdout == "x" * worker.limits.max_output_bytes
    assert docker_supervisor_module.SUPERVISOR_PROTOCOL_VERSION not in execution.stdout
    assert ("ACK" in events) is (extra_bytes == 0)


@pytest.mark.parametrize("adapter_returncode", [0, 1, 125, 255, -signal.SIGTERM])
def test_authenticated_adapter_status_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    adapter_returncode: int,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / f"status-{adapter_returncode}"
    report_root.mkdir()

    class _StatusPopen(_FakePopen):
        pass

    _StatusPopen.adapter_returncode = adapter_returncode
    monkeypatch.setattr(subprocess, "Popen", _StatusPopen)
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_container_oom", lambda *args, **kwargs: False)
    monkeypatch.setattr(worker, "_remove_container", lambda _: None)
    monkeypatch.setattr(worker, "_verify_removed", lambda _: None)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name=f"status-worker-{adapter_returncode}",
        report_path=report_root / "report.json",
        report_root_identity=docker_worker_module._process_control.filesystem_object_identity(  # noqa: SLF001
            report_root.lstat()
        ),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
    )

    assert execution.returncode == adapter_returncode
    assert execution.outcome == "complete"


def test_report_digest_mismatch_never_acknowledges_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "digest-mismatch"
    report_root.mkdir()
    events: list[str] = []

    class _ReportPopen(_FakePopen):
        report_payload = b'{"trusted":true}'

    _ReportPopen.events = events
    monkeypatch.setattr(subprocess, "Popen", _ReportPopen)
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(
        worker,
        "_copy_report",
        lambda _name, destination, _completion, **_kwargs: destination.write_bytes(b'{"tampered":true}'),
    )
    monkeypatch.setattr(worker, "_container_oom", lambda *args, **kwargs: False)
    monkeypatch.setattr(worker, "_remove_container", lambda _: None)
    monkeypatch.setattr(worker, "_verify_removed", lambda _: None)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="digest-worker",
        report_path=report_root / "report.json",
        report_root_identity=docker_worker_module._process_control.filesystem_object_identity(  # noqa: SLF001
            report_root.lstat()
        ),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
    )

    assert execution.report_payload is None
    assert "authenticated identity" in str(execution.error)
    assert "ACK" not in events


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
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_container_oom", lambda _name, **_kwargs: False)
    monkeypatch.setattr(worker, "_remove_container", removed.append)
    monkeypatch.setattr(worker, "_verify_removed", verified.append)

    execution_expires_at = time.time() + 0.01
    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="timeout-worker",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=0.01,
        execution_expires_at=execution_expires_at,
        hard_expires_at=execution_expires_at + 0.02,
    )

    assert execution.outcome == "timeout"
    assert execution.timed_out
    assert removed == ["timeout-worker", "timeout-worker"]
    assert verified == ["timeout-worker"]


def test_watchdog_deadline_race_is_still_classified_as_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "report"
    report_root.mkdir()
    monkeypatch.setattr(subprocess, "Popen", _DeadlineRemovedPopen)
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_remove_container", lambda _: None)
    monkeypatch.setattr(worker, "_verify_removed", lambda _: None)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="watchdog-race",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
        execution_expires_at=time.time() - 0.001,
        hard_expires_at=time.time() + 0.01,
    )

    assert execution.outcome == "timeout"
    assert execution.timed_out


def test_success_observed_after_absolute_deadline_is_still_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker = _worker(monkeypatch, tmp_path)
    report_root = tmp_path / "report"
    report_root.mkdir()
    monkeypatch.setattr(subprocess, "Popen", _LateSuccessPopen)
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_remove_container", lambda _: None)
    monkeypatch.setattr(worker, "_verify_removed", lambda _: None)

    execution = worker._execute_container(  # noqa: SLF001 - absolute deadline race
        ["docker", "run"],
        container_name="late-success",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
    )

    assert execution.outcome == "timeout"
    assert execution.timed_out


def test_process_group_termination_escalates_and_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    signals: list[int] = []

    class _StubbornProcess:
        pid = 1234
        returncode: int | None = None
        waits = 0

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("docker", 1)
            self.returncode = -signal.SIGKILL
            return self.returncode

    process = _StubbornProcess()
    monkeypatch.setattr(docker_watchdog_module.os, "killpg", lambda _pid, sig: signals.append(sig))

    docker_watchdog_module.terminate_process_group(process, description="test process")  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.returncode == -signal.SIGKILL


def test_detached_watchdog_removes_only_its_owned_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    removed: list[list[str]] = []
    ps_calls = 0

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal ps_calls
        del kwargs
        if argv[1:3] == ["ps", "-aq"]:
            ps_calls += 1
            return subprocess.CompletedProcess(argv, 0, stdout="owned-id\n" if ps_calls == 1 else "", stderr="")
        if argv[1:3] == ["rm", "-f"]:
            removed.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(docker_watchdog_module, "CLEANUP_TIMEOUT_SECONDS", 0.005)
    monkeypatch.setattr(docker_watchdog_module, "_POLL_SECONDS", 0.001)
    monkeypatch.setattr(docker_watchdog_module.subprocess, "run", fake_run)

    result = docker_watchdog_module.run_deadline_watchdog(
        "/usr/bin/docker",
        "autoctx-kernel-deadbeef",
        time.time(),
        tmp_path / "ready",
    )

    assert result == 0
    assert removed == [["/usr/bin/docker", "rm", "-f", "owned-id"]]
    assert (tmp_path / "ready").read_text(encoding="ascii") == "ready\n"


def test_detached_watchdog_removes_container_when_coordinator_parent_disappears(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    removed: list[list[str]] = []
    parent_ids = iter((1234, 1))
    listed = False

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal listed
        del kwargs
        if argv[1:3] == ["ps", "-aq"]:
            if not listed:
                listed = True
                return subprocess.CompletedProcess(argv, 0, stdout="owned-id\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[1:3] == ["rm", "-f"]:
            removed.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(docker_watchdog_module, "_POLL_SECONDS", 0.001)
    monkeypatch.setattr(docker_watchdog_module.os, "getppid", lambda: next(parent_ids))
    monkeypatch.setattr(docker_watchdog_module.subprocess, "run", fake_run)

    result = docker_watchdog_module.run_deadline_watchdog(
        "/usr/bin/docker",
        "autoctx-kernel-deadbeef",
        time.time() + 60,
        tmp_path / "ready",
        coordinator_pid=1234,
    )

    assert result == 0
    assert removed == [["/usr/bin/docker", "rm", "-f", "owned-id"]]


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
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_container_oom", lambda _name, **_kwargs: True)
    monkeypatch.setattr(worker, "_copy_report", lambda *_: None)
    monkeypatch.setattr(worker, "_remove_container", removed.append)
    monkeypatch.setattr(worker, "_verify_removed", verified.append)

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="oom-worker",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
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
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_container_oom", lambda _name, **_kwargs: False)
    monkeypatch.setattr(worker, "_copy_report", lambda *_: None)
    monkeypatch.setattr(worker, "_remove_container", removed.append)
    monkeypatch.setattr(worker, "_verify_removed", verified.append)

    execution = worker._execute_container(  # noqa: SLF001 - lifecycle integration seam
        ["docker", "run"],
        container_name="coordinator-failure",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
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
    _disable_real_watchdog(monkeypatch)
    monkeypatch.setattr(worker, "_container_oom", lambda _name, **_kwargs: False)
    monkeypatch.setattr(worker, "_copy_report", lambda *_: None)
    monkeypatch.setattr(worker, "_remove_container", lambda _: None)
    monkeypatch.setattr(worker, "_verify_removed", lambda _: (_ for _ in ()).throw(RuntimeError("still alive")))

    execution = worker._execute_container(  # noqa: SLF001
        ["docker", "run"],
        container_name="teardown-failure",
        report_path=report_root / "report.json",
        report_root_identity=(report_root.stat().st_dev, report_root.stat().st_ino, 0o040000),
        timeout_seconds=1,
        execution_expires_at=time.time() + 1,
        hard_expires_at=time.time() + 2,
    )

    assert execution.outcome == "teardown_failed"
    assert "still alive" in str(execution.error)


@pytest.mark.skipif(
    os.environ.get("AUTOCONTEXT_RUN_GPU_DOCKER_INTEGRATION") != "1",
    reason="requires an explicit real Docker+MIG release-gate environment",
)
def test_real_docker_mig_security_and_crash_cleanup_release_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercise kernel-enforced boundaries on the actual release host, not mocks."""

    device_id = os.environ.get("AUTOCONTEXT_GPU_DEVICE_ID")
    image = os.environ.get("AUTOCONTEXT_GPU_DOCKER_IMAGE")
    if not device_id or not image:
        pytest.fail("set AUTOCONTEXT_GPU_DEVICE_ID and AUTOCONTEXT_GPU_DOCKER_IMAGE for the GPU release gate")
    docker_binary = os.environ.get("AUTOCONTEXT_DOCKER_BINARY", "docker")
    nvidia_smi_binary = os.environ.get("AUTOCONTEXT_NVIDIA_SMI_BINARY", "nvidia-smi")
    python_binary = os.environ.get("AUTOCONTEXT_GPU_DOCKER_PYTHON", "python")
    attestor = NvidiaSMIGPUDeviceAttestor(nvidia_smi_binary)
    probe = attestor.attest(
        DockerGPUDeviceGrant(device_id=device_id, isolation_kind="mig", enforced_memory_bytes=1)
    )
    grant = DockerGPUDeviceGrant(
        device_id=device_id,
        isolation_kind="mig",
        enforced_memory_bytes=probe.enforced_memory_bytes,
    )
    sentinel = tmp_path / "unmounted-host-sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    attack_harness = tmp_path / "attack_adapter.py"
    attack_harness.write_text(
        """
import argparse
import hashlib
import json
import os
import socket
from pathlib import Path

import torch

parser = argparse.ArgumentParser()
parser.add_argument("--candidate")
parser.add_argument("--incumbent")
parser.add_argument("--report")
parser.add_argument("--candidate-artifact-digest")
parser.add_argument("--incumbent-artifact-digest")
args = parser.parse_args()
if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
    raise RuntimeError("the explicit MIG grant did not expose exactly one CUDA device")
if torch.cuda.get_device_properties(0).total_memory != __EXPECTED_CAPACITY__:
    raise RuntimeError("the CUDA-visible device capacity disagrees with host NVML attestation")
print("GPU_PARTITION_ATTESTED")
try:
    socket.create_connection(("1.1.1.1", 53), timeout=0.25)
except OSError:
    print("EGRESS_DENIED")
else:
    raise RuntimeError("network egress unexpectedly succeeded")
try:
    Path(__HOST_SENTINEL__).read_bytes()
except (FileNotFoundError, PermissionError):
    print("HOST_PATH_DENIED")
else:
    raise RuntimeError("unmounted host path unexpectedly readable")
held = []
try:
    for index in range(1024):
        handle = open(f"/output/deleted-{index}", "wb", buffering=0)
        os.unlink(handle.name)
        held.append(handle)
        handle.write(b"x" * 4096)
except OSError:
    print("OUTPUT_TMPFS_QUOTA_ENFORCED")
else:
    raise RuntimeError("deleted-open files bypassed output quota")
finally:
    for handle in held:
        handle.close()
metadata = {
    "device_grant": os.environ["AUTOCONTEXT_GPU_DEVICE_ID"],
    "device_isolation_kind": os.environ["AUTOCONTEXT_GPU_ISOLATION_KIND"],
    "device_enforced_memory_bytes": os.environ["AUTOCONTEXT_GPU_ENFORCED_MEMORY_BYTES"],
    "device_attestor_id": os.environ["AUTOCONTEXT_GPU_ATTESTOR_ID"],
    "device_attestation_digest": os.environ["AUTOCONTEXT_GPU_ATTESTATION_DIGEST"],
}
Path(args.report).write_text(json.dumps({
    "resources": {
        "candidate_artifact_digest": args.candidate_artifact_digest,
        "incumbent_artifact_digest": args.incumbent_artifact_digest,
        "candidate_peak_allocated_bytes": 0,
        "candidate_peak_reserved_bytes": 0,
        "incumbent_peak_allocated_bytes": 0,
        "incumbent_peak_reserved_bytes": 0,
        "device_total_memory_bytes": __EXPECTED_CAPACITY__,
    },
    "hardware": {"metadata": metadata},
}), encoding="utf-8")
""".replace("__HOST_SENTINEL__", repr(str(sentinel))).replace(
            "__EXPECTED_CAPACITY__", str(probe.enforced_memory_bytes)
        ),
        encoding="utf-8",
    )
    limits = DockerKernelWorkerLimits(
        memory_mb=512,
        cpu_count=2,
        cpu_time_seconds=30,
        pids_limit=32,
        max_report_bytes=64 * 1024,
        max_workspace_bytes=8 * 1024**2,
        max_workspace_inodes=128,
        max_gpu_memory_bytes=probe.enforced_memory_bytes,
    )
    worker = DockerKernelBenchmarkRunner(
        [
            python_binary,
            "{immutable_0}",
            "--candidate",
            "{candidate}",
            "--incumbent",
            "{incumbent}",
            "--report",
            "{report}",
            "--candidate-artifact-digest",
            "{candidate_artifact_digest}",
            "--incumbent-artifact-digest",
            "{incumbent_artifact_digest}",
        ],
        image=image,
        immutable_paths=[attack_harness],
        gpu_grant=grant,
        gpu_attestor=attestor,
        limits=limits,
        docker_binary=docker_binary,
    )
    candidate = KernelCandidate(source="def kernel():\n    return None\n")
    generated_ids = iter(("1" * 32, "2" * 32))
    monkeypatch.setattr(docker_worker_module.uuid, "uuid4", lambda: SimpleNamespace(hex=next(generated_ids)))
    attack = worker.run(candidate, candidate, timeout_seconds=20)

    assert attack.outcome == "complete"
    assert attack.report_payload is not None
    assert "GPU_PARTITION_ATTESTED" in attack.stdout
    assert "EGRESS_DENIED" in attack.stdout
    assert "HOST_PATH_DENIED" in attack.stdout
    assert "OUTPUT_TMPFS_QUOTA_ENFORCED" in attack.stdout
    assert sentinel.read_text(encoding="utf-8") == "unchanged"

    timeout_harness = tmp_path / "timeout_adapter.py"
    timeout_harness.write_text(
        """
import subprocess
import time

subprocess.Popen(["python", "-c", "import time; time.sleep(3600)"])
time.sleep(3600)
""",
        encoding="utf-8",
    )
    coordinator_script = tmp_path / "crashing_coordinator.py"
    coordinator_script.write_text(
        """
import sys
from types import SimpleNamespace

from autocontext.kernel_evolution import (
    DockerGPUDeviceGrant,
    DockerKernelBenchmarkRunner,
    DockerKernelWorkerLimits,
    KernelCandidate,
    NvidiaSMIGPUDeviceAttestor,
)
from autocontext.kernel_evolution import docker_worker as docker_worker_module

image, device, capacity, docker, nvidia_smi, python, harness, token = sys.argv[1:]
capacity_int = int(capacity)
docker_worker_module.uuid.uuid4 = lambda: SimpleNamespace(hex=token)
runner = DockerKernelBenchmarkRunner(
    [python, "{immutable_0}", "--candidate", "{candidate}", "--incumbent", "{incumbent}", "--report", "{report}"],
    image=image,
    immutable_paths=[harness],
    gpu_grant=DockerGPUDeviceGrant(device_id=device, isolation_kind="mig", enforced_memory_bytes=capacity_int),
    gpu_attestor=NvidiaSMIGPUDeviceAttestor(nvidia_smi),
    limits=DockerKernelWorkerLimits(
        memory_mb=512,
        cpu_count=2,
        cpu_time_seconds=30,
        pids_limit=32,
        max_report_bytes=65536,
        max_workspace_bytes=8388608,
        max_workspace_inodes=128,
        max_gpu_memory_bytes=capacity_int,
    ),
    docker_binary=docker,
)
candidate = KernelCandidate(source="def kernel():\\n    return None\\n")
runner.run(candidate, candidate, timeout_seconds=2.0)
""",
        encoding="utf-8",
    )
    token = "3" * 32
    container_name = f"autoctx-kernel-{token[:20]}"
    label_filter = f"label={docker_watchdog_module.DOCKER_KERNEL_OWNER_LABEL}={container_name}"
    coordinator = subprocess.Popen(  # noqa: S603 - opt-in release-host crash probe
        [
            sys.executable,
            str(coordinator_script),
            image,
            device_id,
            str(probe.enforced_memory_bytes),
            docker_binary,
            nvidia_smi_binary,
            python_binary,
            str(timeout_harness),
            token,
        ],
        start_new_session=True,
    )
    try:
        startup_deadline = time.monotonic() + 15
        while True:
            present = subprocess.run(
                [docker_binary, "ps", "-q", "--filter", label_filter],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            if present:
                break
            if coordinator.poll() is not None:
                pytest.fail(f"crash-probe coordinator exited early with {coordinator.returncode}")
            if time.monotonic() >= startup_deadline:
                pytest.fail("crash-probe container did not start")
            time.sleep(0.1)

        os.killpg(coordinator.pid, signal.SIGKILL)
        coordinator.wait(timeout=10)
        cleanup_deadline = time.monotonic() + 15
        while subprocess.run(
            [docker_binary, "ps", "-aq", "--filter", label_filter],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip():
            if time.monotonic() >= cleanup_deadline:
                pytest.fail("detached watchdog did not remove the crashed coordinator's container")
            time.sleep(0.1)
    finally:
        if coordinator.poll() is None:
            os.killpg(coordinator.pid, signal.SIGKILL)
            coordinator.wait(timeout=10)
        subprocess.run(
            [docker_binary, "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
