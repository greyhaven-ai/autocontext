from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.kernel_evolution import (
    DockerKernelWorkerLimits,
    KernelCandidate,
    KernelDecisionPolicy,
    KernelSequentialTestingPolicy,
    KernelStatisticsPolicy,
    canonical_digest,
)

_BUNDLE = Path(__file__).resolve().parents[2] / "examples" / "kernel_evolution" / "kernelbench_h100"
_PINNED_IMAGE = f"registry.example/autocontext-kernel@sha256:{'a' * 64}"


class _SerializableNamespace(SimpleNamespace):
    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return dict(vars(self))


@pytest.fixture
def runtime_modules(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    monkeypatch.syspath_prepend(str(_BUNDLE))
    production_runtime = importlib.import_module("production_runtime")
    campaign = importlib.import_module("campaign")
    return production_runtime, campaign


def _runtime(production_runtime: Any, *, gpu_memory_bytes: int = 8 * 1024**3) -> Any:
    return production_runtime.H100DockerRuntimeConfig(
        image=_PINNED_IMAGE,
        docker_binary="docker-production",
        nvidia_smi_binary="nvidia-smi-production",
        container_python="/opt/kernel-venv/bin/python",
        gpu_device="MIG-GPU-deadbeef/1/0",
        gpu_isolation_kind="mig",
        gpu_memory_bytes=gpu_memory_bytes,
        limits=DockerKernelWorkerLimits(max_gpu_memory_bytes=8 * 1024**3),
        timeout_seconds=180.0,
    )


def test_production_runtime_is_pinned_and_protected_composition_is_runnable_for_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, _campaign = runtime_modules
    autokernel_root = tmp_path / "autokernel"
    autokernel_root.mkdir()
    (autokernel_root / "kernel.py").write_text("def kernel_fn(a, b): return a @ b\n", encoding="utf-8")
    private_plan = tmp_path / "private-plan.json"
    private_plan.write_text("{}\n", encoding="utf-8")
    runtime = _runtime(production_runtime)
    runtime_manifest = runtime.manifest()
    assert runtime_manifest["image"] == _PINNED_IMAGE
    assert runtime_manifest["docker_binary"] == "docker-production"
    assert runtime_manifest["nvidia_smi_binary"] == "nvidia-smi-production"
    assert runtime_manifest["gpu_device"] == "MIG-GPU-deadbeef/1/0"
    assert runtime_manifest["gpu_memory_bytes"] == 8 * 1024**3
    assert runtime_manifest["limits"]["max_gpu_memory_bytes"] == 8 * 1024**3
    assert runtime_manifest["evidence_boundary"] == {
        "required": "trusted-evaluator/isolated-accelerator-candidate-v1",
        "available": False,
    }

    captured: dict[str, Any] = {}

    class FakeAttestor:
        def __init__(self, binary: str) -> None:
            captured["attestor_binary"] = binary

    class FakeProtectedRunner:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            captured["command"] = command
            captured["runner"] = kwargs

        def manifest(self) -> dict[str, Any]:
            return {"kind": "fake-protected-runner"}

    monkeypatch.setattr(production_runtime, "NvidiaSMIGPUDeviceAttestor", FakeAttestor)
    monkeypatch.setattr(production_runtime, "DockerProtectedKernelBenchmarkRunner", FakeProtectedRunner)
    evaluator = production_runtime._compose_docker_evaluator(
        runtime=runtime,
        bundle=_BUNDLE,
        adapter_name="adapter.py",
        autokernel_root=autokernel_root,
        private_plan=private_plan,
        problem_id="kernel-problem",
        precision_profile="strict-fp32-v1",
        plan_commitment=f"sha256:{'b' * 64}",
        proposal_cap=10,
        familywise_alpha=0.05,
    )

    manifest = evaluator.manifest()
    assert manifest["evaluator"]["require_authority_receipt"] is True
    assert manifest["evaluator"]["require_resource_telemetry"] is True
    assert manifest["evaluator"]["adaptive_feedback_policy"] == "aggregate-gates"
    assert captured["attestor_binary"] == "nvidia-smi-production"
    assert captured["runner"]["evaluator_immutable_paths"] == (_BUNDLE.resolve(), private_plan.resolve())
    assert captured["runner"]["candidate_runtime_path"] == _BUNDLE / "authority_worker.py"
    command = captured["command"]
    assert "{candidate_socket}" in command and "{incumbent_socket}" in command
    assert "{candidate}" not in command and "{incumbent}" not in command
    assert str(private_plan) not in command


def test_production_campaign_has_no_same_interpreter_override(runtime_modules: tuple[Any, Any]) -> None:
    production_runtime, _campaign = runtime_modules

    with pytest.raises(production_runtime.ProductionEvaluatorBoundaryUnavailable, match="real H100/MIG validation"):
        production_runtime.require_protected_evaluator_boundary()

    source = (_BUNDLE / "campaign.py").read_text(encoding="utf-8")
    assert "ExternalKernelBenchmarkRunner" not in source
    assert "trusted_unsafe" not in source
    assert "allow-untrusted" not in source
    for evidence_field in (
        '"schema_version": "autocontext.kernel-h100-profile-evidence/v3"',
        '"champion":',
        '"primary_receipt":',
        '"confirmation_receipt":',
        '"hardware_attestation":',
        '"decision_policy_id":',
        '"decision_policy":',
    ):
        assert evidence_field in source

    control_source = (_BUNDLE / "control_smoke.py").read_text(encoding="utf-8")
    assert "bootstrap_samples=20_000" in control_source
    assert '"evidence_status": "non_authoritative_trusted_unsafe"' in control_source
    assert '"authoritative": False' in control_source
    assert '"report": observation.report' not in control_source


def test_control_smoke_applies_the_profile_memory_fraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(_BUNDLE))
    control_smoke = importlib.import_module("control_smoke")
    observation = SimpleNamespace(
        eligible=True,
        report=SimpleNamespace(
            resources=SimpleNamespace(
                candidate_enforced_peak_bytes=81,
                device_total_memory_bytes=100,
            )
        ),
        environment_drift_ratio=0.0,
        relative_improvement=0.10,
        speedup_lcb=1.20,
        candidate_p95_ms=1.0,
        incumbent_p95_ms=1.1,
        all_case_no_regression_passed=True,
    )

    assert control_smoke._promotion_decision(observation) == {
        "promote": False,
        "decision": "rejected",
        "reason": "memory_limit",
    }


def test_campaign_fails_before_creating_mailbox_or_launching_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    _production_runtime, campaign = runtime_modules
    autokernel_root = tmp_path / "autokernel"
    autokernel_root.mkdir()
    baseline = autokernel_root / "kernel.py"
    baseline.write_text("def kernel_fn(a, b): return a @ b\n", encoding="utf-8")
    primary_plan = tmp_path / "primary.json"
    confirmation_plan = tmp_path / "confirmation.json"
    primary_plan.write_text("{}\n", encoding="utf-8")
    confirmation_plan.write_text("{}\n", encoding="utf-8")
    mailbox = tmp_path / "must-not-exist"
    monkeypatch.setattr(campaign, "private_plan_commitment", lambda path: f"sha256:{'a' * 64}")
    monkeypatch.setattr(campaign, "load_private_plan", lambda *args, **kwargs: {"test": True})
    monkeypatch.setattr(campaign, "_validate_confirmation_schedule", lambda *args, **kwargs: None)
    monkeypatch.setattr(campaign, "_make_evaluator", lambda **kwargs: object())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "campaign.py",
            "--autokernel-root",
            str(autokernel_root),
            "--baseline",
            str(baseline),
            "--worker-image",
            _PINNED_IMAGE,
            "--container-python",
            "/opt/kernel-venv/bin/python",
            "--gpu-device",
            "MIG-GPU-deadbeef/1/0",
            "--gpu-isolation-kind",
            "mig",
            "--gpu-memory-bytes",
            str(8 * 1024**3),
            "--mailbox",
            str(mailbox),
            "--precision-profile",
            "strict-fp32-v1",
            "--primary-private-plan",
            str(primary_plan),
            "--confirmation-private-plan",
            str(confirmation_plan),
            "--proposals",
            "1",
        ],
    )

    with pytest.raises(SystemExit, match="production H100 campaigns remain disabled"):
        campaign.main()

    assert not mailbox.exists()


def test_post_run_evidence_failure_publishes_failed_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    _production_runtime, campaign = runtime_modules
    autokernel_root = tmp_path / "autokernel"
    autokernel_root.mkdir()
    baseline = autokernel_root / "kernel.py"
    baseline.write_text("def kernel_fn(a, b): return a @ b\n", encoding="utf-8")
    primary_plan = tmp_path / "primary.json"
    confirmation_plan = tmp_path / "confirmation.json"
    primary_plan.write_text("{}\n", encoding="utf-8")
    confirmation_plan.write_text("{}\n", encoding="utf-8")
    mailbox = tmp_path / "mailbox"
    run_dir = tmp_path / "run"

    class FakeRunner:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.run_dir = run_dir
            self.run_dir.mkdir()

        def run(self, *, proposals: int) -> SimpleNamespace:
            assert proposals == 1
            return SimpleNamespace()

    evaluator = SimpleNamespace(manifest=lambda: {"kind": "fake-protected-evaluator"})
    monkeypatch.setattr(campaign, "private_plan_commitment", lambda path: f"sha256:{'a' * 64}")
    monkeypatch.setattr(campaign, "load_private_plan", lambda *args, **kwargs: {"test": True})
    monkeypatch.setattr(campaign, "_validate_confirmation_schedule", lambda *args, **kwargs: None)
    monkeypatch.setattr(campaign, "require_protected_evaluator_boundary", lambda: None)
    monkeypatch.setattr(campaign, "_make_evaluator", lambda **kwargs: evaluator)
    monkeypatch.setattr(campaign, "KernelEvolutionRunner", FakeRunner)
    monkeypatch.setattr(campaign, "_install_sigterm_interrupt", lambda: None)
    monkeypatch.setattr(
        campaign,
        "_progress",
        lambda _run_dir: (_ for _ in ()).throw(OSError("lineage progress is unavailable")),
    )

    def fail_evidence_export(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("receipt export failed")

    monkeypatch.setattr(campaign, "_build_profile_evidence", fail_evidence_export)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "campaign.py",
            "--autokernel-root",
            str(autokernel_root),
            "--baseline",
            str(baseline),
            "--worker-image",
            _PINNED_IMAGE,
            "--container-python",
            "/opt/kernel-venv/bin/python",
            "--gpu-device",
            "MIG-GPU-deadbeef/1/0",
            "--gpu-isolation-kind",
            "mig",
            "--gpu-memory-bytes",
            str(8 * 1024**3),
            "--mailbox",
            str(mailbox),
            "--precision-profile",
            "strict-fp32-v1",
            "--primary-private-plan",
            str(primary_plan),
            "--confirmation-private-plan",
            str(confirmation_plan),
            "--proposals",
            "1",
        ],
    )

    with pytest.raises(RuntimeError, match="receipt export failed"):
        campaign.main()

    status = json.loads((mailbox / "campaign_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["error_type"] == "RuntimeError"
    assert status["error"] == "receipt export failed"


def test_runtime_rejects_claimed_gpu_capacity_above_hard_limit(runtime_modules: tuple[Any, Any]) -> None:
    production_runtime, _campaign = runtime_modules

    with pytest.raises(ValueError, match="cannot exceed max_gpu_memory_bytes"):
        _runtime(production_runtime, gpu_memory_bytes=9 * 1024**3)

    runtime = _runtime(production_runtime)
    with pytest.raises(ValueError, match="absolute normalized path"):
        production_runtime.H100DockerRuntimeConfig(
            image=runtime.image,
            docker_binary=runtime.docker_binary,
            nvidia_smi_binary=runtime.nvidia_smi_binary,
            container_python="python",
            gpu_device=runtime.gpu_device,
            gpu_isolation_kind="mig",
            gpu_memory_bytes=runtime.gpu_memory_bytes,
            limits=runtime.limits,
        )


def _profile_result(campaign: Any, runtime: Any) -> SimpleNamespace:
    primary_commitment = f"sha256:{'b' * 64}"
    confirmation_commitment = f"sha256:{'c' * 64}"
    attestation_payload = {
        "device_id": runtime.gpu_device,
        "isolation_kind": runtime.gpu_isolation_kind,
        "enforced_memory_bytes": runtime.gpu_memory_bytes,
        "attestor_id": "nvidia-smi-nvml-mig-v1",
    }
    attestation = {
        "device_grant": runtime.gpu_device,
        "device_isolation_kind": runtime.gpu_isolation_kind,
        "device_enforced_memory_bytes": str(runtime.gpu_memory_bytes),
        "device_attestor_id": attestation_payload["attestor_id"],
        "device_attestation_digest": canonical_digest(attestation_payload),
    }
    holdout_correctness = _SerializableNamespace(name="holdout", split="holdout", passed=True)
    holdout_performance = _SerializableNamespace(name="holdout", split="holdout", passed_no_regression=True)
    primary_report = SimpleNamespace(
        schema_version="autocontext.kernelbench-eval/v3",
        protocol=SimpleNamespace(seed_commitment=primary_commitment),
        hardware=SimpleNamespace(
            backend="cuda",
            architecture="sm90",
            device_name="NVIDIA H100 80GB HBM3",
            metadata=dict(attestation),
        ),
        correctness=SimpleNamespace(slices=[holdout_correctness]),
        performance=SimpleNamespace(cases=[holdout_performance]),
    )
    confirmation_report = SimpleNamespace(
        schema_version="autocontext.kernelbench-eval/v3",
        protocol=SimpleNamespace(seed_commitment=confirmation_commitment),
        hardware=SimpleNamespace(
            backend="cuda",
            architecture="sm90",
            device_name="NVIDIA H100 80GB HBM3",
            metadata=dict(attestation),
        ),
    )
    confirmation = SimpleNamespace(
        report=confirmation_report,
        protocol_id=f"sha256:{'e' * 64}",
        protocol_compatibility_id=f"sha256:{'f' * 64}",
        hardware_scope_id=f"sha256:{'1' * 64}",
        baseline_id=f"sha256:{'2' * 64}",
    )
    champion = SimpleNamespace(
        schema_version="autocontext.kernel-lineage/v3",
        attempt_id="attempt-winner",
        role="candidate",
        decision="promoted",
        artifact_identity_version="autocontext.kernel-artifact/v2",
        artifact_digest=f"sha256:{'3' * 64}",
        source_digest=f"sha256:{'4' * 64}",
        source_suffix=".py",
        entrypoint="kernel_fn",
        report_digest=f"sha256:{'5' * 64}",
        protocol_id=f"sha256:{'6' * 64}",
        protocol_compatibility_id=f"sha256:{'f' * 64}",
        hardware_scope_id=f"sha256:{'7' * 64}",
        baseline_id=f"sha256:{'2' * 64}",
        observation=SimpleNamespace(report=primary_report),
        confirmation_observation=confirmation,
        confirmation_report_digest=f"sha256:{'8' * 64}",
        confirmation_decision=SimpleNamespace(promote=True),
        sequential_evidence=_SerializableNamespace(proposal_index=1),
    )
    decision_policy = KernelDecisionPolicy(
        statistics=KernelStatisticsPolicy(
            bootstrap_samples=20_000,
            min_timing_blocks=8,
            require_resource_telemetry=True,
            max_gpu_memory_bytes=runtime.gpu_memory_bytes,
        ),
        require_confirmation=True,
        min_relative_improvement=0.05,
        require_confidence=True,
        max_p95_regression=0.05,
        max_environment_drift=0.10,
        max_peak_memory_fraction=0.80,
        target_reference_speedup=2.0,
        sequential_testing=KernelSequentialTestingPolicy(proposal_cap=10, familywise_alpha=0.05),
    )
    champion.decision_policy = decision_policy
    champion.primary_decision = SimpleNamespace(promote=True)
    champion.promotion_decision = SimpleNamespace(promote=True)
    baseline = SimpleNamespace(
        schema_version="autocontext.kernel-lineage/v3",
        attempt_id="attempt-baseline",
        decision="baseline",
        decision_policy=decision_policy,
        primary_decision=SimpleNamespace(promote=True),
        promotion_decision=SimpleNamespace(promote=True),
        observation=SimpleNamespace(report=primary_report),
        confirmation_observation=None,
        sequential_evidence=None,
    )
    return SimpleNamespace(
        schema_version="autocontext.kernel-result/v3",
        run_id="kernel-h100-test",
        problem_id=campaign.PROBLEM_ID,
        precision_profile="strict-fp32-v1",
        champion_attempt_id=champion.attempt_id,
        protocol_id=champion.protocol_id,
        protocol_compatibility_id=champion.protocol_compatibility_id,
        decision_policy=decision_policy,
        attempts=[baseline, champion],
        _primary_commitment=primary_commitment,
        _confirmation_commitment=confirmation_commitment,
    )


def test_profile_evidence_builder_validates_exact_receipts_and_attestation(
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime)
    result = _profile_result(campaign, runtime)

    evidence = campaign._build_profile_evidence(
        result=result,
        run_id=result.run_id,
        precision_profile=result.precision_profile,
        primary_commitment=result._primary_commitment,
        confirmation_commitments=(result._confirmation_commitment,),
        runtime=runtime,
    )

    assert evidence["schema_version"] == "autocontext.kernel-h100-profile-evidence/v3"
    assert evidence["champion"]["attempt_id"] == result.champion_attempt_id
    assert evidence["primary_receipt"]["plan_commitment"] == result._primary_commitment
    assert evidence["confirmation_receipt"]["plan_commitment"] == result._confirmation_commitment
    assert evidence["hardware_attestation"]["device_id"] == runtime.gpu_device
    assert evidence["hardware_attestation"]["attestor_id"] == "nvidia-smi-nvml-mig-v1"
    assert evidence["decision_policy_id"] == result.decision_policy.policy_id
    assert evidence["decision_policy"] == result.decision_policy.model_dump(mode="json")
    assert evidence["proposals_evaluated"] == 1
    assert evidence["promotions"] == 1
    assert evidence["all_holdout_correctness_passed"] is True
    assert evidence["all_holdout_no_regression_passed"] is True

    champion = result.attempts[1]
    champion.observation.report.hardware.metadata["device_grant"] = "MIG-GPU-forged/1/0"
    with pytest.raises(RuntimeError, match="does not match the host-owned runtime grant"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
        )

    result.decision_policy = None
    with pytest.raises(RuntimeError, match="missing its immutable decision policy"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
        )

    result = _profile_result(campaign, runtime)
    result.decision_policy = result.decision_policy.model_copy(update={"min_relative_improvement": 0.0})
    with pytest.raises(RuntimeError, match="does not match the canonical H100 profile"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
        )


def test_profile_evidence_rejects_non_h100_or_incomplete_v3_chain(
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime)
    result = _profile_result(campaign, runtime)
    result.attempts[1].observation.report.hardware.architecture = "sm80"
    result.attempts[1].observation.report.hardware.device_name = "NVIDIA A100-SXM4-80GB"

    with pytest.raises(RuntimeError, match="CUDA SM90 NVIDIA H100"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
        )

    result = _profile_result(campaign, runtime)
    result.attempts[1].schema_version = "autocontext.kernel-lineage/v2"
    with pytest.raises(RuntimeError, match="complete v3 result chain"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
        )


def test_profile_evidence_recomputes_complete_gpu_attestation(
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime)
    result = _profile_result(campaign, runtime)
    champion_report = result.attempts[1].observation.report
    champion_report.hardware.metadata["device_attestor_id"] = "forged-attestor-v1"

    with pytest.raises(RuntimeError, match="digest does not match its exact payload"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
        )


@pytest.mark.skipif(
    os.environ.get("AUTOCONTEXT_RUN_PROTECTED_GPU_INTEGRATION") != "1",
    reason="requires an explicit protected H100/MIG release host",
)
def test_real_h100_protected_authority_adversarial_release_gate(runtime_modules: tuple[Any, Any]) -> None:
    """Exercise the exact guarded production factory on a real H100/MIG host."""

    production_runtime, _campaign = runtime_modules
    profile_contract = importlib.import_module("profile_contract")
    required = {
        name: os.environ.get(name)
        for name in (
            "AUTOCONTEXT_GPU_DOCKER_IMAGE",
            "AUTOCONTEXT_GPU_DOCKER_PYTHON",
            "AUTOCONTEXT_GPU_DEVICE_ID",
            "AUTOCONTEXT_GPU_MEMORY_BYTES",
            "AUTOCONTEXT_AUTOKERNEL_ROOT",
            "AUTOCONTEXT_KERNEL_PRIVATE_PLAN",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"protected H100 release gate is missing environment: {', '.join(missing)}")
    memory_bytes = int(str(required["AUTOCONTEXT_GPU_MEMORY_BYTES"]))
    autokernel_root = Path(str(required["AUTOCONTEXT_AUTOKERNEL_ROOT"])).resolve(strict=True)
    private_plan = Path(str(required["AUTOCONTEXT_KERNEL_PRIVATE_PLAN"])).resolve(strict=True)
    runtime = production_runtime.H100DockerRuntimeConfig(
        image=str(required["AUTOCONTEXT_GPU_DOCKER_IMAGE"]),
        docker_binary=os.environ.get("AUTOCONTEXT_DOCKER_BINARY", "docker"),
        nvidia_smi_binary=os.environ.get("AUTOCONTEXT_NVIDIA_SMI_BINARY", "nvidia-smi"),
        container_python=str(required["AUTOCONTEXT_GPU_DOCKER_PYTHON"]),
        gpu_device=str(required["AUTOCONTEXT_GPU_DEVICE_ID"]),
        gpu_isolation_kind="mig",
        gpu_memory_bytes=memory_bytes,
        limits=DockerKernelWorkerLimits(max_gpu_memory_bytes=memory_bytes),
        timeout_seconds=630.0,
    )
    plan_commitment = profile_contract.private_plan_commitment(private_plan)
    evaluator = production_runtime._compose_docker_evaluator(
        runtime=runtime,
        bundle=_BUNDLE,
        adapter_name="adapter.py",
        autokernel_root=autokernel_root,
        private_plan=private_plan,
        problem_id="kernelbench-v0.1-level1-1-matmul-profiled-h100-v1",
        precision_profile="strict-fp32-v1",
        plan_commitment=plan_commitment,
        proposal_cap=10,
        familywise_alpha=0.05,
    )
    hostile_source = """
import os
import pathlib
import socket
import subprocess
import sys
import torch

_marker = pathlib.Path('/workspace/ac1003-persistence-probe')
_escaped = _marker.exists()
_marker.write_text('candidate-state')
for _private_root in ('/evaluator', '/output', '/channels'):
    if pathlib.Path(_private_root).exists():
        _escaped = True
for _secret_name in ('AUTOCONTEXT_EVALUATOR_BUILD_DIGEST', 'AUTOCONTEXT_BOUNDARY_MANIFEST_DIGEST'):
    if _secret_name in os.environ:
        _escaped = True
try:
    socket.create_connection(('1.1.1.1', 53), timeout=0.05)
    _escaped = True
except OSError:
    pass
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])
torch.cuda.Event = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('candidate clock'))

def kernel_fn(a, b):
    return torch.zeros_like(a @ b) if _escaped else a @ b
""".strip()
    hostile = KernelCandidate(source=hostile_source, entrypoint="kernel_fn")

    first = evaluator.evaluate(hostile, hostile)
    second = evaluator.evaluate(hostile, hostile)

    for observation in (first, second):
        assert observation.eligible, observation.feedback
        assert observation.report is not None
        receipt = observation.report.evaluator_authority_receipt
        assert receipt is not None
        assert {measurement.role for measurement in receipt.measurements} == {"candidate", "incumbent"}
        assert receipt.accelerator_attestation.device_id == runtime.gpu_device


def _plan(seed: int, order: tuple[int, int, int]) -> dict[str, Any]:
    names = (f"train-{seed}", f"holdout-a-{seed}", f"holdout-b-{seed}")
    cases = [
        {
            "name": name,
            "split": "train" if index == 0 else "holdout",
            "seed": seed + index,
            "m": 129 + seed,
            "n": 130 + index,
            "k": 132,
            "a_layout": "contiguous",
            "b_layout": "transposed",
        }
        for index, name in enumerate(names)
    ]
    return {
        "profile_name": "strict-fp32-v1",
        "cases": cases,
        "timing_order": [names[index] for index in order],
    }


def test_confirmation_schedule_requires_pairwise_fresh_plan_per_proposal(runtime_modules: tuple[Any, Any]) -> None:
    _production_runtime, campaign = runtime_modules
    primary = _plan(10, (0, 1, 2))
    confirmation_one = _plan(20, (1, 2, 0))
    confirmation_two = _plan(30, (2, 0, 1))

    with pytest.raises(ValueError, match="one fresh plan per proposal"):
        campaign._validate_confirmation_schedule(primary, (confirmation_one,), proposals=2)

    campaign._validate_confirmation_schedule(primary, (confirmation_one, confirmation_two), proposals=2)
    with pytest.raises(ValueError, match="disjoint inputs"):
        campaign._validate_confirmation_schedule(primary, (confirmation_one, confirmation_one), proposals=2)
