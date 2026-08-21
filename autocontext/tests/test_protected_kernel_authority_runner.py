from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.kernel_evolution import (
    AcceleratorAttestation,
    AuthorityMeasurement,
    DockerGPUDeviceAttestation,
    DockerGPUDeviceGrant,
    DockerKernelWorkerLimits,
    DockerProtectedKernelBenchmarkRunner,
    KernelCandidate,
    build_authority_receipt,
    canonical_authority_digest,
)

_PINNED_IMAGE = f"registry.example/accelerator-evaluator@sha256:{'a' * 64}"


class _Attestor:
    attestor_id = "test-partition-attestor-v1"

    def manifest(self) -> dict[str, Any]:
        return {"attestor_id": self.attestor_id}

    def attest(self, grant: DockerGPUDeviceGrant) -> DockerGPUDeviceAttestation:
        assert grant.enforced_memory_bytes is not None
        return DockerGPUDeviceAttestation(
            device_id=grant.device_id,
            isolation_kind="mig",
            enforced_memory_bytes=grant.enforced_memory_bytes,
            attestor_id=self.attestor_id,
        )


@pytest.fixture
def authority_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DockerProtectedKernelBenchmarkRunner, dict[str, Path]]:
    monkeypatch.setattr(
        "autocontext.kernel_evolution.authority_runner.shutil.which",
        lambda value: f"/usr/bin/{value}",
    )
    evaluator = tmp_path / "trusted-evaluator.py"
    private_plan = tmp_path / "private-confirmation-plan.json"
    reference = tmp_path / "trusted-reference.py"
    runtime = tmp_path / "candidate-authority-worker.py"
    support = tmp_path / "public-candidate-support"
    for path, content in (
        (evaluator, "# trusted evaluator\n"),
        (private_plan, "{\"private\": true}\n"),
        (reference, "# trusted reference\n"),
        (runtime, "# candidate worker\n"),
    ):
        path.write_text(content, encoding="utf-8")
    support.mkdir()
    (support / "kernel_api.py").write_text("# public API\n", encoding="utf-8")
    grant = DockerGPUDeviceGrant(
        device_id="MIG-GPU-deadbeef/1/0",
        isolation_kind="mig",
        enforced_memory_bytes=8 * 1024**3,
    )
    runner = DockerProtectedKernelBenchmarkRunner(
        [
            "/opt/runtime/bin/python",
            "{immutable_0}",
            "--private-plan",
            "{immutable_1}",
            "--reference",
            "{immutable_2}",
            "--candidate-socket",
            "{candidate_socket}",
            "--incumbent-socket",
            "{incumbent_socket}",
            "--report",
            "{report}",
            "--candidate-artifact-digest",
            "{candidate_artifact_digest}",
            "--incumbent-artifact-digest",
            "{incumbent_artifact_digest}",
        ],
        image=_PINNED_IMAGE,
        container_python="/opt/runtime/bin/python",
        evaluator_immutable_paths=(evaluator, private_plan, reference),
        candidate_runtime_path=runtime,
        candidate_support_paths=(support,),
        gpu_grant=grant,
        gpu_attestor=_Attestor(),
        limits=DockerKernelWorkerLimits(max_gpu_memory_bytes=8 * 1024**3),
        docker_binary="docker-test",
    )
    return runner, {
        "evaluator": evaluator,
        "private_plan": private_plan,
        "reference": reference,
        "runtime": runtime,
        "support": support,
    }


def test_candidate_authority_mounts_cannot_reach_private_evaluator_material(
    authority_runner: tuple[DockerProtectedKernelBenchmarkRunner, dict[str, Path]],
    tmp_path: Path,
) -> None:
    runner, paths = authority_runner
    candidate = KernelCandidate(source="def kernel_fn(a, b): return a @ b", entrypoint="kernel_fn")
    staged = tmp_path / "candidate.py"
    staged.write_text(candidate.source, encoding="utf-8")
    channel = tmp_path / "candidate-channel"
    channel.mkdir()
    attestation = _Attestor().attest(runner.gpu_grant)

    command = runner._candidate_docker_command(
        "candidate-container",
        staged,
        channel,
        candidate,
        "candidate",
        attestation,
        2_000_000_000.0,
    )
    rendered = "\0".join(command)

    assert "--network\0none" in rendered
    assert "--cap-drop\0ALL" in rendered
    assert "--read-only" in command
    assert "--security-opt\0no-new-privileges" in rendered
    assert f"type=bind,src={channel},dst=/channel,readonly" in rendered
    assert f"type=bind,src={staged},dst=/artifact/source.py,readonly" in rendered
    assert f"type=bind,src={paths['runtime']},dst=/authority/worker.py,readonly" in rendered
    assert f"type=bind,src={paths['support']},dst=/support/0,readonly" in rendered
    assert "/output" not in rendered
    assert "/evaluator/" not in rendered
    for private_path in (paths["evaluator"], paths["private_plan"], paths["reference"]):
        assert str(private_path) not in rendered
        assert private_path.name not in rendered
    for credential_name in ("AWS_ACCESS_KEY_ID", "GITHUB_TOKEN", "OPENAI_API_KEY"):
        assert credential_name not in rendered


def test_evaluator_authority_has_private_controls_but_no_generated_source(
    authority_runner: tuple[DockerProtectedKernelBenchmarkRunner, dict[str, Path]],
    tmp_path: Path,
) -> None:
    runner, paths = authority_runner
    candidate = KernelCandidate(source="def kernel_fn(a, b): return a @ b", entrypoint="kernel_fn")
    incumbent = KernelCandidate(source="def kernel_fn(a, b): return a.matmul(b)", entrypoint="kernel_fn")
    candidate_channel = tmp_path / "candidate-channel"
    incumbent_channel = tmp_path / "incumbent-channel"
    report = tmp_path / "report"
    for directory in (candidate_channel, incumbent_channel, report):
        directory.mkdir()
    attestation = _Attestor().attest(runner.gpu_grant)

    command = runner._evaluator_docker_command(
        "evaluator-container",
        candidate_channel,
        incumbent_channel,
        report,
        candidate,
        incumbent,
        attestation,
        2_000_000_000.0,
    )
    rendered = "\0".join(command)

    assert f"type=bind,src={paths['evaluator']},dst=/evaluator/0,readonly" in rendered
    assert f"type=bind,src={paths['private_plan']},dst=/evaluator/1,readonly" in rendered
    assert f"type=bind,src={paths['reference']},dst=/evaluator/2,readonly" in rendered
    assert f"type=bind,src={report},dst=/output" in rendered
    assert f"type=bind,src={candidate_channel},dst=/channels/candidate" in rendered
    assert f"type=bind,src={incumbent_channel},dst=/channels/incumbent" in rendered
    assert candidate.source not in rendered
    assert incumbent.source not in rendered
    assert "/artifact/" not in rendered


def test_public_manifest_uses_generic_accelerator_boundary_and_redacts_private_paths(
    authority_runner: tuple[DockerProtectedKernelBenchmarkRunner, dict[str, Path]],
) -> None:
    runner, paths = authority_runner
    manifest = runner.manifest()
    rendered = str(manifest)

    assert manifest["evidence_boundary"] == "trusted-evaluator/isolated-accelerator-candidate-v1"
    assert manifest["kind"] == "docker-protected-accelerator-evaluator"
    assert manifest["candidate_mount_policy"] == "source+runtime+public-support+one-readonly-socket"
    assert "H100" not in rendered
    for private_path in (paths["evaluator"], paths["private_plan"], paths["reference"]):
        assert str(private_path) not in rendered


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("oom", "oom"),
        ("protocol_corruption", "protocol_corruption"),
        ("evaluator_crash", "evaluator_crashed"),
        ("candidate_crash", "candidate_crashed"),
        ("teardown_failure", "teardown_failed"),
        ("timeout", "timeout"),
    ],
)
def test_authority_failures_remain_distinct(failure: str, expected: str) -> None:
    assert (
        DockerProtectedKernelBenchmarkRunner._reported_failure_outcome(
            {"failure_kind": failure},
            default="complete",
        )
        == expected
    )


def _receipt_payload(attestation: DockerGPUDeviceAttestation) -> dict[str, Any]:
    candidate_digest = canonical_authority_digest("candidate")
    incumbent_digest = canonical_authority_digest("incumbent")
    plan_digest = canonical_authority_digest("plan")
    accelerator = AcceleratorAttestation(
        backend="cuda",
        vendor="nvidia",
        architecture="sm90",
        device_id=attestation.device_id,
        isolation_kind=attestation.isolation_kind,
        enforced_memory_bytes=attestation.enforced_memory_bytes,
        runtime="cuda-12.8",
        driver="570.1",
        attestor_id=attestation.attestor_id,
        metadata={"grant_attestation_digest": attestation.digest},
    )
    measurements = tuple(
        AuthorityMeasurement(
            sequence=index,
            role=role,
            request_digest=canonical_authority_digest(f"request-{role}"),
            response_digest=canonical_authority_digest(f"response-{role}"),
            input_commitment=canonical_authority_digest(f"input-{role}"),
            output_commitment=canonical_authority_digest(f"output-{role}"),
            elapsed_ns=10 + index,
            observed_peak_memory_bytes=100 + index,
            outcome="complete",
        )
        for index, role in enumerate(("candidate", "incumbent"))
    )
    report: dict[str, Any] = {
        "candidate_artifact_digest": candidate_digest,
        "incumbent_artifact_digest": incumbent_digest,
        "protocol": {"seed_commitment": plan_digest},
    }
    receipt = build_authority_receipt(
        evaluator_build_digest=canonical_authority_digest("build"),
        boundary_manifest_digest=canonical_authority_digest("boundary"),
        plan_commitment=plan_digest,
        accelerator_attestation=accelerator,
        candidate_artifact_digest=candidate_digest,
        incumbent_artifact_digest=incumbent_digest,
        measurements=measurements,
        report=report,
    )
    report["evaluator_authority_receipt"] = receipt.model_dump(mode="json")
    return report


def test_authority_receipt_is_bound_to_the_host_attested_partition() -> None:
    host_attestation = DockerGPUDeviceAttestation(
        device_id="MIG-GPU-deadbeef/1/0",
        isolation_kind="mig",
        enforced_memory_bytes=8 * 1024**3,
        attestor_id="test-partition-attestor-v1",
    )
    payload = _receipt_payload(host_attestation)

    assert DockerProtectedKernelBenchmarkRunner._validate_receipt(payload, host_attestation) is None

    forged_host_attestation = DockerGPUDeviceAttestation(
        device_id="MIG-GPU-forged/2/0",
        isolation_kind="mig",
        enforced_memory_bytes=8 * 1024**3,
        attestor_id="test-partition-attestor-v1",
    )
    assert "host-attested accelerator grant" in str(
        DockerProtectedKernelBenchmarkRunner._validate_receipt(payload, forged_host_attestation)
    )


def test_hostile_candidate_has_no_persistent_or_report_writable_mount(
    authority_runner: tuple[DockerProtectedKernelBenchmarkRunner, dict[str, Path]],
    tmp_path: Path,
) -> None:
    runner, _paths = authority_runner
    candidate = KernelCandidate(
        source="import torch\ntorch.cuda.Event = lambda *a, **k: None\ndef kernel_fn(a, b): return a @ b",
        entrypoint="kernel_fn",
    )
    source = tmp_path / "hostile.py"
    source.write_text(candidate.source, encoding="utf-8")
    channel = tmp_path / "channel"
    channel.mkdir()
    command = runner._candidate_docker_command(
        "hostile-candidate",
        source,
        channel,
        candidate,
        "candidate",
        _Attestor().attest(runner.gpu_grant),
        2_000_000_000.0,
    )
    rendered = "\0".join(command)

    # Patching torch is confined to this container/process.  No candidate
    # mount can persist it, overwrite report authority, or reach another role.
    assert "--pids-limit" in command
    assert "dst=/workspace" not in rendered
    assert "dst=/output" not in rendered
    assert "dst=/channels/" not in rendered
    assert "--tmpfs\0/workspace:" in rendered
    assert f"type=bind,src={channel},dst=/channel,readonly" in rendered


def test_timeout_kills_every_authority_and_verifies_container_teardown(
    monkeypatch: pytest.MonkeyPatch,
    authority_runner: tuple[DockerProtectedKernelBenchmarkRunner, dict[str, Path]],
    tmp_path: Path,
) -> None:
    runner, _paths = authority_runner
    processes: list[Any] = []

    class FakeProcess:
        def __init__(self, command: list[str], **_kwargs: Any) -> None:
            self.command = command
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.killed = False
            processes.append(self)

        def wait(self, timeout: float | None = None) -> int:
            if self is processes[0]:
                raise subprocess.TimeoutExpired(self.command, timeout or 0.0)
            return 0

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def kill(self) -> None:
            self.killed = True

    monkeypatch.setattr(
        "autocontext.kernel_evolution.authority_runner.subprocess.Popen",
        FakeProcess,
    )
    monkeypatch.setattr(runner, "_wait_for_evaluator_sockets", lambda *args: None)
    watched: list[str] = []

    def launch_watchdog(_binary: str, name: str, _expires: float, _ready: Path) -> object:
        watched.append(name)
        return object()

    monkeypatch.setattr(
        "autocontext.kernel_evolution.authority_runner.launch_deadline_watchdog",
        launch_watchdog,
    )
    monkeypatch.setattr(
        "autocontext.kernel_evolution.authority_runner.terminate_process_group",
        lambda *args, **kwargs: None,
    )
    removed: list[str] = []
    verified: list[str] = []
    monkeypatch.setattr(runner, "_remove_container", removed.append)
    monkeypatch.setattr(runner, "_verify_removed", verified.append)
    attestation = _Attestor().attest(runner.gpu_grant)

    execution = runner._execute_authorities(
        ["docker", "evaluator"],
        ["docker", "candidate"],
        ["docker", "incumbent"],
        names={"evaluator": "eval", "candidate": "cand", "incumbent": "inc"},
        socket_paths=(tmp_path / "candidate.sock", tmp_path / "incumbent.sock"),
        report_path=tmp_path / "report.json",
        report_identity=object(),  # ignored on the timeout path
        timeout_seconds=0.01,
        accelerator_attestation=attestation,
        watchdog_root=tmp_path,
        expires_at=2_000_000_000.0,
    )

    assert execution.outcome == "timeout"
    assert all(process.killed for process in processes)
    assert removed == ["eval", "cand", "inc"]
    assert verified == ["eval", "cand", "inc"]
    assert watched == ["eval", "cand", "inc"]


def test_coordinator_loss_reconciliation_removes_expired_authority_containers(
    monkeypatch: pytest.MonkeyPatch,
    authority_runner: tuple[DockerProtectedKernelBenchmarkRunner, dict[str, Path]],
) -> None:
    runner, _paths = authority_runner
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, stdout="owned-evaluator\nowned-candidate\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="owned-evaluator\t10\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="owned-candidate\t10\n", stderr=""),
        )
    )
    monkeypatch.setattr(
        "autocontext.kernel_evolution.authority_runner.subprocess.run",
        lambda *args, **kwargs: next(responses),
    )
    removed: list[str] = []
    verified: list[str] = []
    monkeypatch.setattr(runner, "_remove_container", removed.append)
    monkeypatch.setattr(runner, "_verify_removed", verified.append)

    assert runner.reconcile(now=20.0) == 2
    assert removed == ["owned-evaluator", "owned-candidate"]
    assert verified == removed
