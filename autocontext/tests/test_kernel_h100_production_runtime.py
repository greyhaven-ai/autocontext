from __future__ import annotations

import copy
import importlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.kernel_evolution import (
    AcceleratorAttestation,
    AuthorityMeasurement,
    DockerKernelWorkerLimits,
    KernelCandidate,
    KernelDecisionPolicy,
    KernelDerivedStatisticsReceipt,
    KernelSequentialTestingPolicy,
    KernelStatisticsPolicy,
    ProfileEvidenceEnvelope,
    build_authority_receipt,
    canonical_authority_digest,
    canonical_digest,
    promotion_margin,
    read_authority_hmac_secret,
    verify_profile_evidence_envelope,
)

_BUNDLE = Path(__file__).resolve().parents[2] / "examples" / "kernel_evolution" / "kernelbench_h100"
_PINNED_IMAGE = f"registry.example/autocontext-kernel@sha256:{'a' * 64}"
_AUTHORITY_KEY_ID = "test-h100-authority-v1"
_AUTHORITY_SECRET = b"test-only-h100-authority-secret-material"


class _SerializableNamespace(SimpleNamespace):
    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"

        def convert(value: Any) -> Any:
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if isinstance(value, list):
                return [convert(item) for item in value]
            if isinstance(value, tuple):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            return value

        return {key: convert(value) for key, value in vars(self).items()}


@pytest.fixture
def runtime_modules(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    monkeypatch.syspath_prepend(str(_BUNDLE))
    production_runtime = importlib.import_module("production_runtime")
    campaign = importlib.import_module("campaign")
    return production_runtime, campaign


def _authority_secret(tmp_path: Path) -> Path:
    path = tmp_path / "authority-hmac.secret"
    path.write_bytes(_AUTHORITY_SECRET)
    path.chmod(0o600)
    return path


def _runtime(
    production_runtime: Any,
    authority_secret_path: Path,
    *,
    gpu_memory_bytes: int = 8 * 1024**3,
) -> Any:
    return production_runtime.H100DockerRuntimeConfig(
        image=_PINNED_IMAGE,
        docker_binary="docker-production",
        nvidia_smi_binary="nvidia-smi-production",
        container_python="/opt/kernel-venv/bin/python",
        gpu_device="MIG-GPU-deadbeef/1/0",
        gpu_isolation_kind="mig",
        gpu_memory_bytes=gpu_memory_bytes,
        authority_hmac_key_id=_AUTHORITY_KEY_ID,
        authority_hmac_secret_path=authority_secret_path,
        limits=DockerKernelWorkerLimits(max_gpu_memory_bytes=8 * 1024**3),
        timeout_seconds=180.0,
    )


def test_production_runtime_is_pinned_and_protected_composition_is_inspectable_while_unavailable(
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
    authority_secret = _authority_secret(tmp_path)
    runtime = _runtime(production_runtime, authority_secret)
    runtime_manifest = runtime.manifest()
    assert runtime_manifest["image"] == _PINNED_IMAGE
    assert runtime_manifest["docker_binary"] == "docker-production"
    assert runtime_manifest["nvidia_smi_binary"] == "nvidia-smi-production"
    assert runtime_manifest["gpu_device"] == "MIG-GPU-deadbeef/1/0"
    assert runtime_manifest["gpu_memory_bytes"] == 8 * 1024**3
    assert runtime_manifest["limits"]["max_gpu_memory_bytes"] == 8 * 1024**3
    assert runtime_manifest["evidence_boundary"]["required"] == (
        "trusted-evaluator/isolated-accelerator-candidate-v1"
    )
    assert runtime_manifest["evidence_boundary"]["available"] is False
    assert runtime_manifest["evidence_boundary"]["requirements"]["crash_safe_container_creation"] == {
        "required": "supervised-create-before-coordinator-ownership/v1",
        "available": False,
        "reason": (
            "protected accelerator evidence requires crash-safe container creation; "
            "the v1 coordinator-owned docker create path is unsupported"
        ),
    }
    assert any(
        "crash-safe" in requirement
        for requirement in runtime_manifest["evidence_boundary"]["unmet_requirements"]
    )
    assert runtime_manifest["authority_authentication"] == {
        "algorithm": "hmac-sha256",
        "key_id": _AUTHORITY_KEY_ID,
    }
    assert str(authority_secret) not in str(runtime_manifest)

    captured: dict[str, Any] = {}

    class FakeAttestor:
        def __init__(self, binary: str) -> None:
            captured["attestor_binary"] = binary

    class FakeProtectedRunner:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            captured["command"] = command
            captured["runner"] = kwargs

        def manifest(self) -> dict[str, Any]:
            return {
                "kind": "fake-protected-runner",
                "evaluator_build_digest": canonical_authority_digest("fake-evaluator-build"),
            }

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
    assert manifest["evaluator"]["authority_trust"]["key_id"] == _AUTHORITY_KEY_ID
    assert str(authority_secret) not in str(manifest)
    assert captured["attestor_binary"] == "nvidia-smi-production"
    assert captured["runner"]["evaluator_immutable_paths"] == (_BUNDLE.resolve(), private_plan.resolve())
    assert captured["runner"]["candidate_runtime_path"] == _BUNDLE / "authority_worker.py"
    assert captured["runner"]["authority_hmac_key_id"] == _AUTHORITY_KEY_ID
    assert captured["runner"]["authority_hmac_secret_path"] == authority_secret
    command = captured["command"]
    assert "{candidate_socket}" in command and "{incumbent_socket}" in command
    assert "{candidate}" not in command and "{incumbent}" not in command
    assert str(private_plan) not in command


def test_production_campaign_has_no_same_interpreter_override(runtime_modules: tuple[Any, Any]) -> None:
    production_runtime, _campaign = runtime_modules

    with pytest.raises(
        production_runtime.ProductionEvaluatorBoundaryUnavailable,
        match="comparable candidate/incumbent/reference timing boundaries",
    ):
        production_runtime.require_protected_evaluator_boundary()

    source = (_BUNDLE / "campaign.py").read_text(encoding="utf-8")
    assert "ExternalKernelBenchmarkRunner" not in source
    assert "trusted_unsafe" not in source
    assert "allow-untrusted" not in source
    for evidence_field in (
        '"schema_version": "autocontext.kernel-h100-profile-evidence/v4"',
        '"champion":',
        '"primary_receipt":',
        '"confirmation_receipt":',
        '"hardware_attestation":',
        '"decision_policy_id":',
        '"decision_policy":',
    ):
        assert evidence_field in source

    control_source = (_BUNDLE / "control_smoke.py").read_text(encoding="utf-8")
    assert 'statistics_method="paired-sign-eprocess/v1"' in control_source
    assert '"evidence_status": "non_authoritative_trusted_unsafe"' in control_source
    assert '"authoritative": False' in control_source
    assert '"report": observation.report' not in control_source


def test_mailbox_generator_stop_and_resume_reuse_the_exact_claim(
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    _production_runtime, campaign = runtime_modules
    mailbox = tmp_path / "mailbox"
    mailbox.mkdir()
    stopped = campaign.MailboxGenerator(
        mailbox,
        timeout_seconds=1.0,
        cancellation_requested=lambda: True,
    )

    with pytest.raises(campaign.KernelGenerationCancelled):
        stopped("exact recursive prompt", 0)

    source = "def kernel_fn(a, b):\n    return a @ b\n"
    (mailbox / "candidate_0.py").write_text(source, encoding="utf-8")
    resumed = campaign.MailboxGenerator(mailbox, timeout_seconds=1.0)
    result = resumed("exact recursive prompt", 0)

    assert result.source == source
    assert result.provider == "mailbox"
    assert result.model == "operator-supplied"
    assert (mailbox / "accepted_candidate_0.json").is_file()


def test_control_smoke_loads_all_contract_modules() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import control_smoke

loaded = control_smoke._load_contract_modules(Path(sys.argv[2]))
assert tuple(module.__name__ for module in loaded) == (
    "autocontext.kernel_evolution.models",
    "autocontext.kernel_evolution.promotion_margin",
    "autocontext.kernel_evolution.benchmark",
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(_BUNDLE), str(source_root)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


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
        derived_statistics_receipt=SimpleNamespace(finite_sample_gate_passed=True),
        candidate_p95_ms=1.0,
        incumbent_p95_ms=1.1,
        all_case_no_regression_passed=True,
    )

    assert control_smoke._promotion_decision(
        observation,
        margin_contract=promotion_margin,
    ) == {
        "promote": False,
        "decision": "rejected",
        "reason": "memory_limit",
    }


def test_control_smoke_uses_exact_finite_sample_aggregate_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(_BUNDLE))
    control_smoke = importlib.import_module("control_smoke")

    def observation(candidate_ms: float) -> SimpleNamespace:
        return SimpleNamespace(
            eligible=True,
            report=SimpleNamespace(
                resources=SimpleNamespace(
                    candidate_enforced_peak_bytes=None,
                    device_total_memory_bytes=None,
                ),
                performance=SimpleNamespace(
                    blocks=[
                        SimpleNamespace(candidate_ms=candidate_ms, incumbent_ms=1.0, reference_ms=1.0)
                        for _ in range(8)
                    ]
                ),
            ),
            environment_drift_ratio=0.0,
            relative_improvement=0.05,
            derived_statistics_receipt=SimpleNamespace(finite_sample_gate_passed=True),
            candidate_p95_ms=candidate_ms,
            incumbent_p95_ms=1.0,
            all_case_no_regression_passed=True,
        )

    assert control_smoke._promotion_decision(
        observation(0.95),
        margin_contract=promotion_margin,
    )["promote"]
    assert control_smoke._promotion_decision(
        observation(math.nextafter(0.95, math.inf)),
        margin_contract=promotion_margin,
    ) == {
        "promote": False,
        "decision": "rejected",
        "reason": "insufficient_improvement",
    }


@pytest.mark.parametrize(
    ("first_reference", "last_reference", "stored_drift", "expected_reason"),
    [
        (1.0, 1.10, 0.10000000000000009, "significant_improvement"),
        (4.333163826127088, 4.766480208739797, 0.09999999999999987, "unstable_environment"),
    ],
)
def test_control_smoke_uses_exact_reference_drift_gate(
    monkeypatch: pytest.MonkeyPatch,
    first_reference: float,
    last_reference: float,
    stored_drift: float,
    expected_reason: str,
) -> None:
    monkeypatch.syspath_prepend(str(_BUNDLE))
    control_smoke = importlib.import_module("control_smoke")
    blocks = [
        SimpleNamespace(candidate_ms=0.9, incumbent_ms=1.0, reference_ms=reference)
        for reference in ([first_reference] * 6 + [last_reference] * 2)
    ]
    observation = SimpleNamespace(
        eligible=True,
        report=SimpleNamespace(
            resources=SimpleNamespace(
                candidate_enforced_peak_bytes=None,
                device_total_memory_bytes=None,
            ),
            performance=SimpleNamespace(blocks=blocks),
        ),
        environment_drift_ratio=stored_drift,
        relative_improvement=0.10,
        derived_statistics_receipt=SimpleNamespace(finite_sample_gate_passed=True),
        candidate_p95_ms=0.9,
        incumbent_p95_ms=1.0,
        all_case_no_regression_passed=True,
    )

    assert control_smoke._promotion_decision(observation, margin_contract=promotion_margin)["reason"] == expected_reason


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
    authority_secret = _authority_secret(tmp_path)
    mailbox = tmp_path / "must-not-exist"
    monkeypatch.setattr(campaign, "private_plan_commitment", lambda path: f"sha256:{'a' * 64}")
    monkeypatch.setattr(campaign, "load_private_plan", lambda *args, **kwargs: {"test": True})
    monkeypatch.setattr(campaign, "_validate_confirmation_schedule", lambda *args, **kwargs: None)
    monkeypatch.setattr(campaign, "_make_evaluator", lambda **kwargs: object())
    monkeypatch.setattr(
        campaign,
        "create_provider",
        lambda *args, **kwargs: pytest.fail("provider credentials resolved before protected preflight"),
    )
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
            "--authority-hmac-key-id",
            _AUTHORITY_KEY_ID,
            "--authority-hmac-secret-file",
            str(authority_secret),
            "--mailbox",
            str(mailbox),
            "--sealed-audit-root",
            str(tmp_path / "sealed-audit"),
            "--precision-profile",
            "strict-fp32-v1",
            "--primary-private-plan",
            str(primary_plan),
            "--confirmation-private-plan",
            str(confirmation_plan),
            "--proposals",
            "1",
            "--generator",
            "provider",
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
    authority_secret = _authority_secret(tmp_path)
    mailbox = tmp_path / "mailbox"
    run_dir = tmp_path / "run"

    class FakeRunner:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.run_dir = run_dir
            self.run_dir.mkdir()

        def run(self, *, proposals: int) -> SimpleNamespace:
            assert proposals == 1
            return SimpleNamespace()

    evaluator = SimpleNamespace(
        manifest=lambda: {"kind": "fake-protected-evaluator"},
        config=SimpleNamespace(
            expected_evaluator_build_digest=canonical_authority_digest("fake-evaluator-build"),
            expected_boundary_manifest_digest=canonical_authority_digest("fake-boundary"),
        ),
    )
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
            "--authority-hmac-key-id",
            _AUTHORITY_KEY_ID,
            "--authority-hmac-secret-file",
            str(authority_secret),
            "--mailbox",
            str(mailbox),
            "--sealed-audit-root",
            str(tmp_path / "sealed-audit"),
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


def test_runtime_rejects_claimed_gpu_capacity_above_hard_limit(
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, _campaign = runtime_modules
    authority_secret = _authority_secret(tmp_path)

    with pytest.raises(ValueError, match="cannot exceed max_gpu_memory_bytes"):
        _runtime(production_runtime, authority_secret, gpu_memory_bytes=9 * 1024**3)

    runtime = _runtime(production_runtime, authority_secret)
    with pytest.raises(ValueError, match="absolute normalized path"):
        production_runtime.H100DockerRuntimeConfig(
            image=runtime.image,
            docker_binary=runtime.docker_binary,
            nvidia_smi_binary=runtime.nvidia_smi_binary,
            container_python="python",
            gpu_device=runtime.gpu_device,
            gpu_isolation_kind="mig",
            gpu_memory_bytes=runtime.gpu_memory_bytes,
            authority_hmac_key_id=runtime.authority_hmac_key_id,
            authority_hmac_secret_path=runtime.authority_hmac_secret_path,
            limits=runtime.limits,
        )


def _profile_result(campaign: Any, runtime: Any) -> SimpleNamespace:
    primary_commitment = f"sha256:{'b' * 64}"
    confirmation_commitment = f"sha256:{'c' * 64}"
    candidate_artifact_digest = f"sha256:{'3' * 64}"
    incumbent_artifact_digest = f"sha256:{'9' * 64}"
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
    accelerator = AcceleratorAttestation(
        backend="cuda",
        vendor="nvidia",
        architecture="sm90",
        device_id=runtime.gpu_device,
        isolation_kind=runtime.gpu_isolation_kind,
        enforced_memory_bytes=runtime.gpu_memory_bytes,
        runtime="cuda-12.8",
        driver="570.1",
        attestor_id=attestation_payload["attestor_id"],
        metadata={"grant_attestation_digest": attestation["device_attestation_digest"]},
    )
    resources = {
        "candidate_observed_peak_bytes": 101,
        "incumbent_observed_peak_bytes": 102,
        "telemetry_authority": "trusted-evaluator-observed/v1",
        "accelerator_attestation_digest": accelerator.digest,
        "device_total_memory_bytes": runtime.gpu_memory_bytes,
    }
    primary_report = _SerializableNamespace(
        schema_version="autocontext.kernelbench-eval/v4",
        evaluation_status="complete",
        failure_kind=None,
        candidate_artifact_digest=candidate_artifact_digest,
        incumbent_artifact_digest=incumbent_artifact_digest,
        protocol=_SerializableNamespace(seed_commitment=primary_commitment),
        resources=dict(resources),
        hardware=_SerializableNamespace(
            backend="cuda",
            architecture="sm90",
            device_name="NVIDIA H100 80GB HBM3",
            metadata=dict(attestation),
        ),
        correctness=_SerializableNamespace(slices=[holdout_correctness]),
        performance=_SerializableNamespace(cases=[holdout_performance]),
    )
    confirmation_report = _SerializableNamespace(
        schema_version="autocontext.kernelbench-eval/v4",
        evaluation_status="complete",
        failure_kind=None,
        candidate_artifact_digest=candidate_artifact_digest,
        incumbent_artifact_digest=incumbent_artifact_digest,
        protocol=_SerializableNamespace(seed_commitment=confirmation_commitment),
        resources=dict(resources),
        hardware=_SerializableNamespace(
            backend="cuda",
            architecture="sm90",
            device_name="NVIDIA H100 80GB HBM3",
            metadata=dict(attestation),
        ),
    )
    evaluator_build_digest = canonical_authority_digest("profile-evaluator-build")
    authority_identities = {
        primary_commitment: {
            "evaluator_build_digest": evaluator_build_digest,
            "boundary_manifest_digest": canonical_authority_digest("primary-boundary"),
        },
        confirmation_commitment: {
            "evaluator_build_digest": evaluator_build_digest,
            "boundary_manifest_digest": canonical_authority_digest("confirmation-boundary"),
        },
    }

    def attach_authority_receipt(report: _SerializableNamespace, *, commitment: str, label: str) -> None:
        measurements = (
            AuthorityMeasurement(
                sequence=0,
                role="candidate",
                request_digest=canonical_authority_digest(f"{label}-candidate-request"),
                response_digest=canonical_authority_digest(f"{label}-candidate-response"),
                input_commitment=canonical_authority_digest(f"{label}-candidate-input"),
                output_commitment=canonical_authority_digest(f"{label}-candidate-output"),
                elapsed_ns=10,
                observed_peak_memory_bytes=101,
                outcome="complete",
            ),
            AuthorityMeasurement(
                sequence=1,
                role="incumbent",
                request_digest=canonical_authority_digest(f"{label}-incumbent-request"),
                response_digest=canonical_authority_digest(f"{label}-incumbent-response"),
                input_commitment=canonical_authority_digest(f"{label}-incumbent-input"),
                output_commitment=canonical_authority_digest(f"{label}-incumbent-output"),
                elapsed_ns=11,
                observed_peak_memory_bytes=102,
                outcome="complete",
            ),
        )
        identity = authority_identities[commitment]
        report.evaluator_authority_receipt = build_authority_receipt(
            evaluator_build_digest=identity["evaluator_build_digest"],
            boundary_manifest_digest=identity["boundary_manifest_digest"],
            plan_commitment=commitment,
            accelerator_attestation=accelerator,
            candidate_artifact_digest=candidate_artifact_digest,
            incumbent_artifact_digest=incumbent_artifact_digest,
            measurements=measurements,
            report=report.model_dump(mode="json"),
            signing_key_id=runtime.authority_hmac_key_id,
            signing_secret=read_authority_hmac_secret(runtime.authority_hmac_secret_path),
        )

    attach_authority_receipt(primary_report, commitment=primary_commitment, label="primary")
    attach_authority_receipt(confirmation_report, commitment=confirmation_commitment, label="confirmation")
    confirmation = SimpleNamespace(
        eligible=True,
        report=confirmation_report,
        derived_statistics_receipt=_SerializableNamespace(receipt_id=f"sha256:{'d' * 64}"),
        protocol_id=f"sha256:{'e' * 64}",
        protocol_compatibility_id=f"sha256:{'f' * 64}",
        hardware_scope_id=f"sha256:{'1' * 64}",
        baseline_id=f"sha256:{'2' * 64}",
    )
    champion = SimpleNamespace(
        schema_version="autocontext.kernel-lineage/v4",
        attempt_id="attempt-winner",
        role="candidate",
        decision="promoted",
        artifact_identity_version="autocontext.kernel-artifact/v2",
        artifact_digest=candidate_artifact_digest,
        source_digest=f"sha256:{'4' * 64}",
        source_suffix=".py",
        entrypoint="kernel_fn",
        report_digest=f"sha256:{'5' * 64}",
        protocol_id=f"sha256:{'6' * 64}",
        protocol_compatibility_id=f"sha256:{'f' * 64}",
        hardware_scope_id=f"sha256:{'7' * 64}",
        baseline_id=f"sha256:{'2' * 64}",
        observation=SimpleNamespace(
            eligible=True,
            report=primary_report,
            derived_statistics_receipt=_SerializableNamespace(receipt_id=f"sha256:{'a' * 64}"),
        ),
        confirmation_observation=confirmation,
        confirmation_report_digest=f"sha256:{'8' * 64}",
        confirmation_decision=SimpleNamespace(promote=True),
        sequential_evidence=_SerializableNamespace(proposal_index=1),
    )
    decision_policy = KernelDecisionPolicy(
        schema_version="autocontext.kernel-decision-policy/v2",
        evidence_family_version="autocontext.kernel-evidence-family/v4",
        statistics=KernelStatisticsPolicy(
            schema_version="autocontext.kernel-statistics-policy/v2",
            method="paired-sign-eprocess/v1",
            bootstrap_samples=None,
            seed_derivation="sha256-plan-commitment-block-schedule/v1",
            min_timing_blocks=8,
            require_resource_telemetry=True,
            max_gpu_memory_bytes=runtime.gpu_memory_bytes,
            block_definition="balanced-interleaved-paired-block/v1",
            dependence_assumption="conditional-threshold-win-probability-lte-half/v1",
            null_win_probability=0.5,
            betting_fraction=1.0,
            improvement_margin=0.05,
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
    champion.decision_policy_id = decision_policy.policy_id
    def statistics_receipt(label: str, report_digest: str) -> KernelDerivedStatisticsReceipt:
        return KernelDerivedStatisticsReceipt(
            statistics_policy_id=decision_policy.statistics.policy_id,
            raw_report_digest=report_digest,
            raw_blocks_digest=canonical_digest({"blocks": label}),
            schedule_seed_material_digest=canonical_digest({"seed": label}),
            sample_count=8,
            improvement_margin=0.05,
            null_win_probability=0.5,
            betting_fraction=1.0,
            candidate_wins=8,
            non_wins=0,
            terminal_e_value_zeroed=False,
            log_terminal_e_value=8 * math.log(2.0),
            p_value_bound=1 / 256,
            per_look_alpha=0.005,
            finite_sample_gate_passed=True,
            candidate_median_ms=0.8,
            incumbent_median_ms=1.0,
            reference_median_ms=2.0,
            speedup_vs_incumbent=1.25,
            speedup_vs_reference=2.5,
            relative_improvement=0.2,
            candidate_p95_ms=0.8,
            incumbent_p95_ms=1.0,
            environment_drift_ratio=0.0,
            all_case_no_regression_passed=True,
        )

    champion.observation.derived_statistics_receipt = statistics_receipt("primary", champion.report_digest)
    confirmation.derived_statistics_receipt = statistics_receipt("confirmation", champion.confirmation_report_digest)
    champion.primary_decision = SimpleNamespace(promote=True)
    champion.promotion_decision = SimpleNamespace(promote=True)
    baseline = SimpleNamespace(
        schema_version="autocontext.kernel-lineage/v4",
        attempt_id="attempt-baseline",
        decision="baseline",
        decision_policy=decision_policy,
        decision_policy_id=decision_policy.policy_id,
        primary_decision=SimpleNamespace(promote=True),
        promotion_decision=SimpleNamespace(promote=True),
        observation=SimpleNamespace(
            eligible=True,
            report=primary_report,
            derived_statistics_receipt=_SerializableNamespace(receipt_id=f"sha256:{'b' * 64}"),
        ),
        confirmation_observation=None,
        sequential_evidence=None,
    )
    baseline.observation.derived_statistics_receipt = statistics_receipt("baseline", f"sha256:{'5' * 64}")
    return SimpleNamespace(
        schema_version="autocontext.kernel-result/v4",
        run_id="kernel-h100-test",
        problem_id=campaign.PROBLEM_ID,
        precision_profile="strict-fp32-v1",
        champion_attempt_id=champion.attempt_id,
        protocol_id=champion.protocol_id,
        protocol_compatibility_id=champion.protocol_compatibility_id,
        decision_policy=decision_policy,
        decision_policy_id=decision_policy.policy_id,
        attempts=[baseline, champion],
        _primary_commitment=primary_commitment,
        _confirmation_commitment=confirmation_commitment,
        _authority_identities=authority_identities,
    )


def test_profile_evidence_builder_validates_exact_receipts_and_attestation(
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime, _authority_secret(tmp_path))
    result = _profile_result(campaign, runtime)

    evidence = campaign._build_profile_evidence(
        result=result,
        run_id=result.run_id,
        precision_profile=result.precision_profile,
        primary_commitment=result._primary_commitment,
        confirmation_commitments=(result._confirmation_commitment,),
        runtime=runtime,
        authority_identities=result._authority_identities,
    )

    assert evidence["schema_version"] == "autocontext.kernel-profile-evidence-envelope/v1"
    assert evidence["authentication"]["key_id"] == _AUTHORITY_KEY_ID
    verified = verify_profile_evidence_envelope(
        ProfileEvidenceEnvelope.model_validate(evidence),
        trusted_key_id=_AUTHORITY_KEY_ID,
        trusted_secret=_AUTHORITY_SECRET,
    )
    profile = verified.profile
    assert profile["schema_version"] == "autocontext.kernel-h100-profile-evidence/v4"
    assert profile["champion"]["attempt_id"] == result.champion_attempt_id
    assert profile["primary_receipt"]["plan_commitment"] == result._primary_commitment
    assert profile["confirmation_receipt"]["plan_commitment"] == result._confirmation_commitment
    assert profile["primary_receipt"]["authority_receipt"]["authentication"]["key_id"] == _AUTHORITY_KEY_ID
    assert profile["confirmation_receipt"]["authority_receipt_digest"].startswith("sha256:")
    assert profile["hardware_attestation"]["device_id"] == runtime.gpu_device
    assert profile["hardware_attestation"]["attestor_id"] == "nvidia-smi-nvml-mig-v1"
    assert profile["decision_policy_id"] == result.decision_policy.policy_id
    assert profile["decision_policy"] == result.decision_policy.model_dump(mode="json")
    assert profile["proposals_evaluated"] == 1
    assert profile["promotions"] == 1
    assert profile["all_holdout_correctness_passed"] is True
    assert profile["all_holdout_no_regression_passed"] is True
    campaign._verify_v4_profile_policy_receipts(profile)

    forged_policy = copy.deepcopy(profile)
    forged_policy["decision_policy_id"] = f"sha256:{'0' * 64}"
    with pytest.raises(RuntimeError, match="canonical replay"):
        campaign._verify_v4_profile_policy_receipts(forged_policy)

    forged_calibration = copy.deepcopy(profile)
    forged_calibration["calibration_report"]["exact_familywise_bound"] = 0.0
    with pytest.raises(RuntimeError, match="canonical replay"):
        campaign._verify_v4_profile_policy_receipts(forged_calibration)

    forged_receipt = copy.deepcopy(profile)
    forged_receipt["primary_receipt"]["report_digest"] = f"sha256:{'0' * 64}"
    with pytest.raises(RuntimeError, match="canonical replay"):
        campaign._verify_v4_profile_policy_receipts(forged_receipt)

    mutations = (
        lambda payload: payload["profile"]["champion"].__setitem__("source_digest", "sha256:" + "0" * 64),
        lambda payload: payload["profile"]["decision_policy"].__setitem__("min_relative_improvement", 0.0),
        lambda payload: payload["profile"].__setitem__("promotions", 999),
        lambda payload: payload["profile"].__setitem__("all_holdout_correctness_passed", False),
        lambda payload: payload["profile"]["primary_receipt"].__setitem__(
            "report_digest", "sha256:" + "1" * 64
        ),
        lambda payload: payload["profile"]["primary_receipt"].__setitem__(
            "authority_receipt_digest", "sha256:" + "2" * 64
        ),
    )
    for mutate in mutations:
        forged = copy.deepcopy(evidence)
        mutate(forged)
        forged["content_digest"] = canonical_authority_digest(forged["profile"])
        with pytest.raises(ValueError, match="authentication tag"):
            verify_profile_evidence_envelope(
                forged,
                trusted_key_id=_AUTHORITY_KEY_ID,
                trusted_secret=_AUTHORITY_SECRET,
            )

    with pytest.raises(ValueError, match="authentication key"):
        verify_profile_evidence_envelope(
            evidence,
            trusted_key_id="different-operator-key",
            trusted_secret=_AUTHORITY_SECRET,
        )
    forged_tag = copy.deepcopy(evidence)
    forged_tag["authentication"]["tag"] = "hmac-sha256:" + "0" * 64
    with pytest.raises(ValueError, match="authentication tag"):
        verify_profile_evidence_envelope(
            forged_tag,
            trusted_key_id=_AUTHORITY_KEY_ID,
            trusted_secret=_AUTHORITY_SECRET,
        )

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
            authority_identities=result._authority_identities,
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
            authority_identities=result._authority_identities,
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
            authority_identities=result._authority_identities,
        )

    result = _profile_result(campaign, runtime)
    result.attempts[1].confirmation_observation.report.evaluator_authority_receipt = None
    with pytest.raises(RuntimeError, match="missing a trusted-evaluator authority receipt"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
            authority_identities=result._authority_identities,
        )

    result = _profile_result(campaign, runtime)
    report = result.attempts[1].observation.report
    receipt = report.evaluator_authority_receipt
    report.evaluator_authority_receipt = receipt.model_copy(
        update={
            "authentication": receipt.authentication.model_copy(update={"tag": "hmac-sha256:" + "0" * 64})
        }
    )
    with pytest.raises(RuntimeError, match="failed authenticated replay"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
            authority_identities=result._authority_identities,
        )


def test_profile_evidence_rejects_non_h100_or_incomplete_v4_chain(
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime, _authority_secret(tmp_path))
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
            authority_identities=result._authority_identities,
        )

    result = _profile_result(campaign, runtime)
    result.attempts[1].schema_version = "autocontext.kernel-lineage/v2"
    with pytest.raises(RuntimeError, match="complete v4 result chain"):
        campaign._build_profile_evidence(
            result=result,
            run_id=result.run_id,
            precision_profile=result.precision_profile,
            primary_commitment=result._primary_commitment,
            confirmation_commitments=(result._confirmation_commitment,),
            runtime=runtime,
            authority_identities=result._authority_identities,
        )


@pytest.mark.parametrize("report_schema", [None, "autocontext.kernelbench-eval/v4"])
def test_complete_v4_chain_allows_schema_valid_ineligible_attempts(
    report_schema: str | None,
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime, _authority_secret(tmp_path))
    result = _profile_result(campaign, runtime)
    report = SimpleNamespace(schema_version=report_schema) if report_schema is not None else None
    rejected = SimpleNamespace(
        schema_version="autocontext.kernel-lineage/v4",
        decision_policy=result.decision_policy,
        decision_policy_id=result.decision_policy_id,
        primary_decision=SimpleNamespace(promote=False),
        promotion_decision=SimpleNamespace(promote=False),
        observation=SimpleNamespace(
            eligible=False,
            report=report,
            derived_statistics_receipt=None,
        ),
        confirmation_observation=None,
    )
    result.attempts.insert(1, rejected)

    campaign._require_complete_v4_result_chain(result)


def test_complete_v4_chain_still_requires_receipt_for_eligible_attempt(
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime, _authority_secret(tmp_path))
    result = _profile_result(campaign, runtime)
    result.attempts[0].observation.derived_statistics_receipt = None

    with pytest.raises(RuntimeError, match="complete v4 result chain"):
        campaign._require_complete_v4_result_chain(result)


def test_complete_v4_chain_requires_report_backed_confirmation_identity(
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime, _authority_secret(tmp_path))
    result = _profile_result(campaign, runtime)
    confirmation = result.attempts[1].confirmation_observation
    confirmation.eligible = False
    confirmation.report = None
    confirmation.derived_statistics_receipt = None

    with pytest.raises(RuntimeError, match="report-backed confirmation identity"):
        campaign._require_complete_v4_result_chain(result)


def test_profile_evidence_recomputes_complete_gpu_attestation(
    tmp_path: Path,
    runtime_modules: tuple[Any, Any],
) -> None:
    production_runtime, campaign = runtime_modules
    runtime = _runtime(production_runtime, _authority_secret(tmp_path))
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
            authority_identities=result._authority_identities,
        )


@pytest.mark.skipif(
    os.environ.get("AUTOCONTEXT_RUN_PROTECTED_GPU_INTEGRATION") != "1",
    reason="requires an explicit protected H100/MIG release host",
)
def test_reserved_h100_protected_authority_gate_remains_unavailable(runtime_modules: tuple[Any, Any]) -> None:
    """Reserve the future live gate while proving current execution stops before Docker."""

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
            "AUTOCONTEXT_AUTHORITY_HMAC_KEY_ID",
            "AUTOCONTEXT_AUTHORITY_HMAC_SECRET_FILE",
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
        authority_hmac_key_id=str(required["AUTOCONTEXT_AUTHORITY_HMAC_KEY_ID"]),
        authority_hmac_secret_path=Path(str(required["AUTOCONTEXT_AUTHORITY_HMAC_SECRET_FILE"])),
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
        assert not observation.eligible
        assert observation.rejection_reason == "resource_policy_unsupported"
        assert "independently attested" in observation.feedback


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
