from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.kernel_evolution import (
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkExecution,
    KernelCandidate,
    KernelDecisionPolicy,
    KernelEvidenceVersionError,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelSequentialTestingPolicy,
    KernelStatisticsPolicy,
    calibrate_kernel_promotion,
    canonical_digest,
    content_digest,
    minimum_sign_eprocess_blocks,
    read_kernel_evolution_result,
)


def _latency(candidate: KernelCandidate) -> float:
    marker = candidate.source.split("latency:", maxsplit=1)[1].splitlines()[0]
    return float(marker.strip())


class V4BenchmarkRunner:
    def __init__(self, *, seed: str, confirmation_case: str = "") -> None:
        self.seed = content_digest(seed)
        self.confirmation_case = confirmation_case

    def manifest(self) -> dict[str, Any]:
        return {"kind": "finite-sample-test-runner", "seed_commitment": self.seed}

    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        assert timeout_seconds > 0
        hardware = {
            "backend": "cuda",
            "architecture": "sm90",
            "device_name": "Synthetic H100",
            "runtime": "cuda-test",
            "driver": "test-driver",
            "toolchain": "test-toolchain",
            "workload_family_id": content_digest("finite-sample-family"),
            "workload_fingerprint": content_digest("finite-sample-workload"),
            "metadata": {},
        }
        candidate_ms = _latency(candidate)
        incumbent_ms = _latency(incumbent)
        protocol = {
            "correctness_trials": 2,
            "hidden_trials": 1,
            "warmup_runs": 3,
            "timing_blocks": 8,
            "calls_per_block": 10,
            "atol": 0.0001,
            "rtol": 0.0001,
            "seed_commitment": self.seed,
            "sequential_testing": {
                "method": "bonferroni",
                "proposal_cap": 10,
                "familywise_alpha": 0.05,
            },
        }
        payload = {
            "schema_version": "autocontext.kernelbench-eval/v4",
            "evaluation_status": "complete",
            "failure_kind": None,
            "problem_id": "finite-sample-kernel",
            "artifact_identity_version": candidate.artifact_identity_version,
            "candidate_artifact_digest": candidate.artifact_digest,
            "incumbent_artifact_digest": incumbent.artifact_digest,
            "candidate_source_digest": candidate.source_digest,
            "incumbent_source_digest": incumbent.source_digest,
            "candidate_source_suffix": candidate.source_suffix,
            "incumbent_source_suffix": incumbent.source_suffix,
            "candidate_entrypoint": candidate.entrypoint,
            "incumbent_entrypoint": incumbent.entrypoint,
            "baseline_id": content_digest("finite-sample-reference"),
            "hardware": hardware,
            "hardware_scope_id": canonical_digest(hardware),
            "protocol": protocol,
            "compile": {
                "candidate_passed": True,
                "incumbent_passed": True,
                "candidate_compile_ms": 1.0,
                "diagnostics": "",
            },
            "correctness": {
                "passed": True,
                "tests_run": 2,
                "tests_passed": 2,
                "hidden_tests_run": 1,
                "hidden_tests_passed": 1,
                "parameter_state_match": True,
                "input_mutation_detected": False,
                "failures": [],
            },
            "performance": {
                "blocks": [
                    {
                        "block": block,
                        "candidate_ms": candidate_ms,
                        "incumbent_ms": incumbent_ms,
                        "reference_ms": 2.0,
                    }
                    for block in range(8)
                ]
            },
            "resources": {},
            "metadata": {
                "confirmation_case": self.confirmation_case,
                "measurement_design": {
                    "schema_version": "autocontext.kernel-measurement-design/v1",
                    "block_definition": "balanced-interleaved-paired-block/v1",
                    "schedule_seed_derivation": "sha256-plan-commitment-block-schedule/v1",
                    "dependence_assumption": "conditional-threshold-win-probability-lte-half/v1",
                    "fixed_block_count": 8,
                    "early_stopping_allowed": False,
                    "order_balanced": True,
                },
            },
        }
        return KernelBenchmarkExecution(returncode=0, report_payload=payload)


def _evaluator(runner: V4BenchmarkRunner) -> KernelBenchmarkEvaluator:
    return KernelBenchmarkEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(
            problem_id="finite-sample-kernel",
            min_timing_blocks=8,
            bootstrap_samples=None,
            statistics_method="paired-sign-eprocess/v1",
            finite_sample_improvement_margin=0.05,
        ),
    )


def _candidate(latency: float) -> KernelCandidate:
    return KernelCandidate(source=f"# latency: {latency}\nclass ModelNew: pass\n")


def test_eight_pre_registered_wins_resolve_ten_look_familywise_budget() -> None:
    evaluator = _evaluator(V4BenchmarkRunner(seed="primary"))
    observation = evaluator.evaluate(_candidate(0.8), _candidate(1.0))

    assert observation.eligible
    assert observation.speedup_lcb is None
    assert observation.speedup_lcb95 is None
    receipt = observation.derived_statistics_receipt
    assert receipt is not None
    assert receipt.candidate_wins == receipt.sample_count == 8
    assert receipt.p_value_bound == pytest.approx(1 / 256)
    assert receipt.per_look_alpha == pytest.approx(0.005)
    assert receipt.finite_sample_gate_passed
    assert minimum_sign_eprocess_blocks(0.005) == 8


def test_one_margin_miss_fails_closed_without_bootstrap_claim() -> None:
    runner = V4BenchmarkRunner(seed="primary")
    original = runner.run

    def one_miss(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        execution.report_payload["performance"]["blocks"][7]["candidate_ms"] = 0.96
        return execution

    runner.run = one_miss  # type: ignore[method-assign]
    observation = _evaluator(runner).evaluate(_candidate(0.8), _candidate(1.0))

    assert observation.eligible
    assert observation.derived_statistics_receipt is not None
    assert observation.derived_statistics_receipt.non_wins == 1
    assert observation.derived_statistics_receipt.p_value_bound == 1.0
    assert not observation.derived_statistics_receipt.finite_sample_gate_passed


def test_v4_failed_report_remains_auditable_without_derived_statistics() -> None:
    runner = V4BenchmarkRunner(seed="compile-failure")
    original = runner.run

    def compile_failure(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        execution.report_payload.update(
            {
                "evaluation_status": "candidate_error",
                "failure_kind": "compile",
                "correctness": None,
                "performance": None,
            }
        )
        execution.report_payload["compile"]["candidate_passed"] = False
        return execution

    runner.run = compile_failure  # type: ignore[method-assign]
    observation = _evaluator(runner).evaluate(_candidate(0.8), _candidate(1.0))

    assert not observation.eligible
    assert observation.rejection_reason == "compile_failed"
    assert observation.report is not None
    assert observation.report.schema_version == "autocontext.kernelbench-eval/v4"
    assert observation.derived_statistics_receipt is None


def test_calibration_covers_required_noise_and_adaptive_proposals() -> None:
    policy = KernelDecisionPolicy(
        schema_version="autocontext.kernel-decision-policy/v2",
        evidence_family_version="autocontext.kernel-evidence-family/v4",
        statistics=KernelStatisticsPolicy(
            schema_version="autocontext.kernel-statistics-policy/v2",
            method="paired-sign-eprocess/v1",
            bootstrap_samples=None,
            seed_derivation="sha256-plan-commitment-block-schedule/v1",
            min_timing_blocks=8,
            require_resource_telemetry=False,
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

    report = calibrate_kernel_promotion(policy, trials=1_024, seed_material="test-calibration")

    assert report.exact_per_look_bound == pytest.approx(1 / 256)
    assert report.exact_familywise_bound == pytest.approx(10 / 256)
    assert {scenario.name for scenario in report.scenarios} == {
        "null",
        "heavy-tail",
        "drift",
        "autocorrelation",
        "heteroskedasticity",
    }
    assert report.report_id.startswith("sha256:")


def test_confirmation_details_stay_out_of_adaptive_run_dir_until_terminal(tmp_path: Path) -> None:
    primary = _evaluator(V4BenchmarkRunner(seed="primary"))
    confirmation = _evaluator(
        V4BenchmarkRunner(seed="confirmation", confirmation_case="private-confirmation-case-name")
    )
    public_root = tmp_path / "public"
    sealed_root = tmp_path / "operator-private"
    observed_during_generation: list[str] = []

    def generate(_prompt: str, generation: int) -> str:
        if generation == 1:
            run_dir = next(public_root.iterdir())
            public_text = "\n".join(
                path.read_text(encoding="utf-8") for path in run_dir.rglob("*") if path.is_file()
            )
            observed_during_generation.append(public_text)
            assert "private-confirmation-case-name" not in public_text
            sealed_text = "\n".join(
                path.read_text(encoding="utf-8") for path in sealed_root.rglob("*") if path.is_file()
            )
            assert "private-confirmation-case-name" in sealed_text
        return "# latency: 0.8\nclass ModelNew: pass\n" if generation == 0 else "# latency: 0.81\nclass ModelNew: pass\n"

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate):
        baseline = confirmation.evaluate(incumbent, incumbent)
        assert baseline.eligible
        return confirmation.evaluate(
            candidate,
            incumbent,
            expected_scope_id=baseline.hardware_scope_id,
            expected_baseline_id=baseline.baseline_id,
            expected_protocol_id=baseline.protocol_id,
        )

    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source="# latency: 1.0\nclass ModelNew: pass\n",
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        generate,
        primary,
        public_root,
        confirmation_fn=confirm,
        sealed_audit_root=sealed_root,
    )

    result = runner.run(proposals=2)

    assert observed_during_generation
    released = list((runner.run_dir / "audit" / "confirmation").glob("*.json"))
    assert len(released) == 1
    assert "private-confirmation-case-name" in released[0].read_text(encoding="utf-8")
    assert result.schema_version == "autocontext.kernel-result/v4"
    assert result.decision_policy_id == result.decision_policy.policy_id
    summary = json.loads((runner.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["decision_policy_id"] == result.decision_policy_id

    read = read_kernel_evolution_result(result.model_dump_json())
    assert read.verification_status == "v4-finite-sample-policy-replay-verified"
    assert read.decision_policy_id == result.decision_policy_id
    with pytest.raises(KernelEvidenceVersionError, match="requires a newer reader"):
        read_kernel_evolution_result(result.model_dump_json(), max_supported_version=3)

    forged_policy = result.model_dump(mode="json")
    forged_policy["decision_policy_id"] = content_digest("forged-policy")
    with pytest.raises(ValueError, match="decision-policy digest"):
        read_kernel_evolution_result(forged_policy)

    forged_block = result.model_dump(mode="json")
    forged_block["attempts"][1]["observation"]["report"]["performance"]["blocks"][0]["candidate_ms"] = 0.7
    with pytest.raises(ValueError, match="different raw report"):
        read_kernel_evolution_result(forged_block)

    duplicated = result.model_dump_json().replace(
        '{"schema_version":',
        '{"schema_version":"autocontext.kernel-result/v3","schema_version":',
        1,
    )
    with pytest.raises(KernelEvidenceVersionError, match="duplicate JSON key"):
        read_kernel_evolution_result(duplicated)

    non_finite = result.model_dump_json().replace('"champion_score":1.0', '"champion_score":NaN', 1)
    with pytest.raises(KernelEvidenceVersionError, match="non-finite JSON number"):
        read_kernel_evolution_result(non_finite)

    forged_rejected_receipt = result.model_dump(mode="json")
    rejected_receipt = forged_rejected_receipt["attempts"][2]["observation"]["derived_statistics_receipt"]
    rejected_receipt["raw_blocks_digest"] = content_digest("forged-rejected-raw-blocks")
    with pytest.raises(ValueError, match="does not replay from raw blocks"):
        read_kernel_evolution_result(forged_rejected_receipt)


def test_finite_sample_confirmation_requires_disjoint_audit_root(tmp_path: Path) -> None:
    public_root = tmp_path / "public"
    with pytest.raises(ValueError, match="disjoint"):
        KernelEvolutionRunner(
            KernelEvolutionConfig(
                problem_id="finite-sample-kernel",
                task_prompt="Optimize the test kernel.",
                baseline_source="# latency: 1.0\nclass ModelNew: pass\n",
                min_relative_improvement=0.05,
                proposal_cap=10,
                familywise_alpha=0.05,
            ),
            lambda _prompt, _generation: "# latency: 0.8\nclass ModelNew: pass\n",
            _evaluator(V4BenchmarkRunner(seed="primary")),
            public_root,
            confirmation_fn=lambda candidate, incumbent: _evaluator(
                V4BenchmarkRunner(seed="confirmation")
            ).evaluate(candidate, incumbent),
            sealed_audit_root=public_root / "audit",
        )
