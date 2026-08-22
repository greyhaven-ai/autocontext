from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Literal

import pytest

from autocontext.kernel_evolution import (
    STRICT_FP32_SEMANTICS,
    KernelAttemptRecord,
    KernelBaselineError,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkExecution,
    KernelBenchmarkObservation,
    KernelBenchmarkReport,
    KernelCandidate,
    KernelDecisionPolicy,
    KernelEvidenceVersionError,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelIntegrityError,
    KernelPromotionPolicy,
    KernelSequentialEvidence,
    KernelSequentialTestingPolicy,
    KernelStatisticsPolicy,
    calibrate_kernel_promotion,
    canonical_digest,
    content_digest,
    derive_finite_sample_receipt,
    minimum_sign_eprocess_blocks,
    read_kernel_evolution_result,
)
from autocontext.kernel_evolution.confirmation import _confirmation_veto, evaluate_confirmation_observation
from autocontext.kernel_evolution.evidence_replay import validate_attempt
from autocontext.kernel_evolution.finite_sample import _all_win_p_value_bound


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


def _evaluator(
    runner: V4BenchmarkRunner,
    *,
    adaptive_feedback_policy: Literal["detailed", "aggregate-gates"] = "detailed",
) -> KernelBenchmarkEvaluator:
    return KernelBenchmarkEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(
            problem_id="finite-sample-kernel",
            min_timing_blocks=8,
            bootstrap_samples=None,
            statistics_method="paired-sign-eprocess/v1",
            finite_sample_improvement_margin=0.05,
            adaptive_feedback_policy=adaptive_feedback_policy,
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


def test_exact_dyadic_alpha_boundary_passes_without_rounding_up() -> None:
    statistics_policy = _evaluator(V4BenchmarkRunner(seed="primary")).config.statistics_policy
    alpha = 2**-8

    receipt = derive_finite_sample_receipt(
        blocks=[(0.8, 1.0, 2.0)] * 8,
        statistics_policy=statistics_policy,
        raw_report_digest=content_digest("dyadic-boundary-report"),
        schedule_seed_material="dyadic-boundary-schedule",
        per_look_alpha=alpha,
        all_case_no_regression_passed=True,
    )

    assert minimum_sign_eprocess_blocks(alpha) == 8
    assert receipt.p_value_bound == alpha
    assert receipt.finite_sample_gate_passed


def test_finite_sample_probability_representation_boundary_fails_closed() -> None:
    smallest_positive = math.ulp(0.0)
    statistics_policy = KernelStatisticsPolicy(
        schema_version="autocontext.kernel-statistics-policy/v2",
        method="paired-sign-eprocess/v1",
        bootstrap_samples=None,
        seed_derivation="sha256-plan-commitment-block-schedule/v1",
        min_timing_blocks=1074,
        require_resource_telemetry=False,
        block_definition="balanced-interleaved-paired-block/v1",
        dependence_assumption="conditional-threshold-win-probability-lte-half/v1",
        null_win_probability=0.5,
        betting_fraction=1.0,
        improvement_margin=0.05,
    )

    receipt = derive_finite_sample_receipt(
        blocks=[(0.8, 1.0, 2.0)] * 1074,
        statistics_policy=statistics_policy,
        raw_report_digest=content_digest("binary64-boundary-report"),
        schedule_seed_material="binary64-boundary-schedule",
        per_look_alpha=smallest_positive,
        all_case_no_regression_passed=True,
    )

    assert minimum_sign_eprocess_blocks(smallest_positive) == 1074
    assert _all_win_p_value_bound(0.5, 1074) == smallest_positive
    assert receipt.p_value_bound == smallest_positive
    assert receipt.finite_sample_gate_passed
    with pytest.raises(ValueError, match="underflows binary64"):
        _all_win_p_value_bound(0.5, 1075)
    with pytest.raises(ValueError, match="at most 1074 timing blocks"):
        KernelBenchmarkEvaluatorConfig(
            problem_id="finite-sample-kernel",
            min_timing_blocks=1075,
            bootstrap_samples=None,
            statistics_method="paired-sign-eprocess/v1",
            finite_sample_improvement_margin=0.05,
        )


def test_subnormal_positive_margin_does_not_count_equal_blocks_as_wins() -> None:
    statistics_policy = KernelStatisticsPolicy(
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
        improvement_margin=math.ulp(0.0),
    )

    receipt = derive_finite_sample_receipt(
        blocks=[(1.0, 1.0, 2.0)] * 8,
        statistics_policy=statistics_policy,
        raw_report_digest=content_digest("subnormal-margin-report"),
        schedule_seed_material="subnormal-margin-schedule",
        per_look_alpha=0.005,
        all_case_no_regression_passed=True,
    )

    assert receipt.candidate_wins == 0
    assert receipt.non_wins == 8
    assert receipt.p_value_bound == 1.0
    assert not receipt.finite_sample_gate_passed


def test_tiny_sequential_budgets_fail_before_rounded_evidence_can_be_forged(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="representable as positive"):
        KernelSequentialTestingPolicy(
            proposal_cap=2,
            familywise_alpha=math.ulp(0.0),
        )
    with pytest.raises(ValueError, match="confidence level below one"):
        KernelSequentialTestingPolicy(
            proposal_cap=1,
            familywise_alpha=2**-60,
        )
    with pytest.raises(ValueError, match="confidence level above one half"):
        KernelSequentialTestingPolicy(
            proposal_cap=1,
            familywise_alpha=math.nextafter(0.5, 0.0),
        )
    with pytest.raises(ValueError, match="maximum cumulative alpha spend"):
        KernelSequentialTestingPolicy(
            proposal_cap=3,
            familywise_alpha=math.nextafter(0.5, 0.0),
        )

    with pytest.raises(ValueError, match="per_proposal_alpha disagrees"):
        KernelSequentialEvidence(
            proposal_index=1,
            proposal_cap=1_024,
            familywise_alpha=2**-50,
            per_proposal_alpha=2**-50,
            cumulative_alpha_spent=2**-50,
            confidence_level=math.nextafter(1.0, 0.0),
        )

    unsafe_root = tmp_path / "unsafe-rounded-budget"
    with pytest.raises(ValueError, match="maximum cumulative alpha spend"):
        KernelEvolutionRunner(
            KernelEvolutionConfig(
                problem_id="finite-sample-kernel",
                task_prompt="Optimize the test kernel.",
                baseline_source=_candidate(1.0).source,
                min_relative_improvement=0.05,
                proposal_cap=3,
                familywise_alpha=math.nextafter(0.5, 0.0),
            ),
            lambda _prompt, _generation: _candidate(0.8).source,
            _evaluator(V4BenchmarkRunner(seed="unsafe-rounded-budget")),
            unsafe_root,
        )
    assert not unsafe_root.exists()


def test_finite_sample_receipt_rejects_noncanonical_probability_and_log_claims() -> None:
    statistics_policy = _evaluator(V4BenchmarkRunner(seed="primary")).config.statistics_policy
    receipt = derive_finite_sample_receipt(
        blocks=[(0.8, 1.0, 2.0)] * 8,
        statistics_policy=statistics_policy,
        raw_report_digest=content_digest("canonical-receipt-report"),
        schedule_seed_material="canonical-receipt-schedule",
        per_look_alpha=0.005,
        all_case_no_regression_passed=True,
    )

    forged_probability = receipt.model_dump(mode="python")
    forged_probability["p_value_bound"] = math.nextafter(receipt.p_value_bound, math.inf)
    with pytest.raises(ValueError, match="p-value bound does not replay"):
        type(receipt).model_validate(forged_probability)

    forged_log = receipt.model_dump(mode="python")
    forged_log["log_terminal_e_value"] = math.nextafter(receipt.log_terminal_e_value, math.inf)
    with pytest.raises(ValueError, match="log terminal e-value does not replay"):
        type(receipt).model_validate(forged_log)

    forged_null = receipt.model_dump(mode="python")
    forged_null["null_win_probability"] = 0.25
    forged_null["p_value_bound"] = 0.25**receipt.sample_count
    forged_null["log_terminal_e_value"] = receipt.sample_count * math.log(4.0)
    with pytest.raises(ValueError, match="receipts require p0=0.5"):
        type(receipt).model_validate(forged_null)

    boundary_probability = receipt.model_copy(
        update={"sample_count": 1074, "candidate_wins": 1074, "p_value_bound": math.ulp(0.0)}
    ).model_dump(mode="python")
    boundary_probability["p_value_bound"] = 0.0
    with pytest.raises(ValueError, match="greater than 0"):
        type(receipt).model_validate(boundary_probability)


def test_below_margin_candidate_cannot_win_blocks_or_promote(tmp_path: Path) -> None:
    exact_boundary = _candidate(0.95)
    below_margin = _candidate(math.nextafter(0.95, math.inf))
    incumbent = _candidate(1.0)
    evaluator = _evaluator(V4BenchmarkRunner(seed="primary"))

    exact_observation = evaluator.evaluate(exact_boundary, incumbent)
    below_margin_observation = evaluator.evaluate(below_margin, incumbent)

    assert exact_observation.derived_statistics_receipt is not None
    assert exact_observation.derived_statistics_receipt.candidate_wins == 8
    assert below_margin_observation.derived_statistics_receipt is not None
    assert below_margin_observation.derived_statistics_receipt.candidate_wins == 0
    assert not below_margin_observation.derived_statistics_receipt.finite_sample_gate_passed

    exact_runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=incumbent.source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: exact_boundary.source,
        evaluator,
        tmp_path / "exact-public",
    )
    exact_result = exact_runner.run(proposals=1)
    exact_gates = {gate.name: gate.status for gate in exact_result.attempts[1].primary_decision.gates}

    assert exact_result.champion_attempt_id == exact_result.attempts[1].attempt_id
    assert exact_gates["relative_improvement"] == "passed"
    for attempt_path in (exact_runner.run_dir / "attempts").glob("*.json"):
        KernelAttemptRecord.model_validate_json(attempt_path.read_text(encoding="utf-8"))
    for line in (exact_runner.run_dir / "lineage.jsonl").read_text(encoding="utf-8").splitlines():
        KernelAttemptRecord.model_validate_json(line)
    KernelAttemptRecord.model_validate_json(
        (exact_runner.run_dir / "champion.json").read_text(encoding="utf-8")
    )

    below_runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=incumbent.source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: below_margin.source,
        evaluator,
        tmp_path / "public",
    )
    result = below_runner.run(proposals=1)
    below_gates = {gate.name: gate.status for gate in result.attempts[1].primary_decision.gates}

    assert result.champion_attempt_id == result.baseline_attempt_id
    assert result.attempts[1].promotion_decision is not None
    assert not result.attempts[1].promotion_decision.promote
    assert below_gates["relative_improvement"] == "failed"


def test_mixed_blocks_at_exact_aggregate_margin_pass_the_relative_gate(tmp_path: Path) -> None:
    benchmark = V4BenchmarkRunner(seed="mixed-boundary")
    original = benchmark.run

    def mixed_boundary(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        candidate_times = [0.5, 1.805, *([0.95] * 6)]
        for block, candidate_ms in zip(
            execution.report_payload["performance"]["blocks"],
            candidate_times,
            strict=True,
        ):
            block["candidate_ms"] = candidate_ms
            block["incumbent_ms"] = 1.0
        return execution

    benchmark.run = mixed_boundary  # type: ignore[method-assign]
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(benchmark),
        tmp_path / "public",
    )

    result = runner.run(proposals=1)
    gates = {gate.name: gate.status for gate in result.attempts[1].primary_decision.gates}

    assert gates["relative_improvement"] == "passed"
    assert gates["finite_sample_evidence"] == "failed"


def test_v4_subfloor_case_without_named_semantics_cannot_promote(tmp_path: Path) -> None:
    benchmark = V4BenchmarkRunner(seed="exact-case-floor")
    original = benchmark.run
    candidate_payloads: list[dict[str, Any]] = []

    def subfloor_case(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        if candidate.artifact_digest != incumbent.artifact_digest:
            actual_speedup = 0.9799999999994999
            execution.report_payload["performance"]["cases"] = [
                {
                    "name": "protected-holdout",
                    "split": "holdout",
                    "candidate_median_ms": 1.0,
                    "incumbent_median_ms": actual_speedup,
                    "reference_median_ms": 2.0,
                    "minimum_speedup_vs_incumbent": 0.98,
                    "passed_no_regression": True,
                }
            ]
            candidate_payloads.append(copy.deepcopy(execution.report_payload))
        return execution

    benchmark.run = subfloor_case  # type: ignore[method-assign]
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(benchmark),
        tmp_path / "public",
    )

    result = runner.run(proposals=1)

    assert result.champion_attempt_id == result.baseline_attempt_id
    assert result.attempts[1].reason == "contract_error"
    assert result.attempts[1].observation.report is None
    assert "case no-regression result does not match" in result.attempts[1].observation.feedback
    assert len(candidate_payloads) == 1

    forged_v4 = candidate_payloads[0]
    assert forged_v4["protocol"].get("semantics") is None
    with pytest.raises(ValueError, match="case no-regression result does not match"):
        KernelBenchmarkReport.model_validate(forged_v4)

    legacy_v3 = copy.deepcopy(forged_v4)
    legacy_v3["schema_version"] = "autocontext.kernelbench-eval/v3"
    KernelBenchmarkReport.model_validate(legacy_v3)
    legacy_v3["performance"]["cases"][0]["passed_no_regression"] = False
    with pytest.raises(ValueError, match="case no-regression result does not match"):
        KernelBenchmarkReport.model_validate(legacy_v3)


@pytest.mark.parametrize("tolerance", ("atol", "rtol"))
def test_v4_named_profile_tolerances_and_case_floor_are_exact(tmp_path: Path, tolerance: str) -> None:
    benchmark = V4BenchmarkRunner(seed=f"exact-profile-contract-{tolerance}")
    original = benchmark.run
    baseline_payloads: list[dict[str, Any]] = []
    candidate_payloads: list[dict[str, Any]] = []

    def mutate_named_profile(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        payload = execution.report_payload
        payload["protocol"]["semantics"] = STRICT_FP32_SEMANTICS.model_dump(mode="json")
        payload["correctness"]["slices"] = [
            {"name": "train-case", "split": "train", "cases_run": 1, "cases_passed": 1, "passed": True},
            {"name": "holdout-case", "split": "holdout", "cases_run": 1, "cases_passed": 1, "passed": True},
        ]
        payload["performance"]["cases"] = [
            {
                "name": name,
                "split": split,
                "candidate_median_ms": _latency(candidate),
                "incumbent_median_ms": _latency(incumbent),
                "reference_median_ms": 2.0,
                "minimum_speedup_vs_incumbent": 0.98,
                "passed_no_regression": True,
            }
            for name, split in (("train-case", "train"), ("holdout-case", "holdout"))
        ]
        if candidate.artifact_digest == incumbent.artifact_digest:
            baseline_payloads.append(copy.deepcopy(payload))
        else:
            payload["protocol"][tolerance] = math.nextafter(0.0001, math.inf)
            candidate_payloads.append(copy.deepcopy(payload))
        return execution

    benchmark.run = mutate_named_profile  # type: ignore[method-assign]
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            precision_profile="strict-fp32-v1",
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(benchmark),
        tmp_path / tolerance,
    )

    result = runner.run(proposals=1)

    assert result.champion_attempt_id == result.baseline_attempt_id
    assert result.attempts[1].reason == "contract_error"
    assert "tolerances must exactly match" in result.attempts[1].observation.feedback
    assert len(baseline_payloads) == len(candidate_payloads) == 1

    mutated = candidate_payloads[0]
    with pytest.raises(ValueError, match="tolerances must exactly match"):
        KernelBenchmarkReport.model_validate(mutated)
    legacy_v3 = copy.deepcopy(mutated)
    legacy_v3["schema_version"] = "autocontext.kernelbench-eval/v3"
    KernelBenchmarkReport.model_validate(legacy_v3)

    forged_floor = copy.deepcopy(baseline_payloads[0])
    forged_floor["performance"]["cases"][0]["minimum_speedup_vs_incumbent"] = math.nextafter(0.98, math.inf)
    with pytest.raises(ValueError, match="case no-regression floor does not match"):
        KernelBenchmarkReport.model_validate(forged_floor)

    legacy_floor = copy.deepcopy(forged_floor)
    legacy_floor["schema_version"] = "autocontext.kernelbench-eval/v3"
    KernelBenchmarkReport.model_validate(legacy_floor)


def test_v4_metric_and_summary_bindings_reject_sub_tolerance_forgery(tmp_path: Path) -> None:
    unstable = V4BenchmarkRunner(seed="sub-tolerance-drift")
    original = unstable.run

    def drift_above_gate(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        if candidate.artifact_digest != incumbent.artifact_digest:
            reference_times = [1.0] * 6 + [1.1000000000005] * 2
            for block, reference_ms in zip(
                execution.report_payload["performance"]["blocks"],
                reference_times,
                strict=True,
            ):
                block["reference_ms"] = reference_ms
        return execution

    unstable.run = drift_above_gate  # type: ignore[method-assign]
    unstable_runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            max_environment_drift=0.10,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(unstable),
        tmp_path / "unstable",
    )
    unstable_result = unstable_runner.run(proposals=1)
    attempt = unstable_result.attempts[1]
    assert attempt.reason == "unstable_environment"
    assert attempt.observation.environment_drift_ratio is not None
    assert 0.10 < attempt.observation.environment_drift_ratio < 0.10 + 1e-12

    forged_payload = attempt.observation.model_dump(mode="python")
    forged_payload["environment_drift_ratio"] = 0.10
    with pytest.raises(ValueError, match="environment_drift_ratio disagrees with its derivation receipt"):
        KernelBenchmarkObservation.model_validate(forged_payload)

    forged_observation = attempt.observation.model_copy(update={"environment_drift_ratio": 0.10})
    assert unstable_result.decision_policy is not None
    forged_decision = KernelPromotionPolicy(unstable_result.decision_policy).decide(forged_observation)
    assert forged_decision.reason == "unstable_environment"
    forged_attempt = attempt.model_copy(
        update={
            "observation": forged_observation,
            "primary_decision": forged_decision,
            "promotion_decision": forged_decision,
            "decision": forged_decision.decision,
            "reason": forged_decision.reason,
        }
    )
    with pytest.raises(ValueError, match="environment drift does not replay from its raw report"):
        validate_attempt(forged_attempt)

    stripped_receipt = unstable_result.model_dump(mode="json")
    stripped_attempt = stripped_receipt["attempts"][1]
    stripped_observation = stripped_attempt["observation"]
    stripped_observation.update(
        eligible=False,
        rejection_reason="runner_error",
        feedback="Benchmark report was unavailable.",
        report=None,
    )
    with pytest.raises(ValueError, match="ineligible v4 observations cannot carry"):
        KernelBenchmarkObservation.model_validate(stripped_observation)
    stripped_observation.pop("derived_statistics_receipt")
    ineligible = KernelBenchmarkObservation.model_validate(stripped_observation)
    stripped_decision = KernelPromotionPolicy(unstable_result.decision_policy).decide(ineligible)
    stripped_attempt.update(
        report_digest=None,
        score=None,
        observation=ineligible.model_dump(mode="json"),
        primary_decision=stripped_decision.model_dump(mode="json"),
        promotion_decision=stripped_decision.model_dump(mode="json"),
        decision=stripped_decision.decision,
        reason=stripped_decision.reason,
        sequential_evidence=None,
    )
    with pytest.raises(ValueError, match="bounded decision-policy attempts require sequential-testing evidence"):
        read_kernel_evolution_result(stripped_receipt)

    forged_baseline_receipt = unstable_result.model_dump(mode="json")
    forged_baseline_receipt["attempts"][0]["sequential_evidence"] = unstable_result.attempts[1].sequential_evidence
    with pytest.raises(ValueError, match="baseline attempts cannot spend"):
        read_kernel_evolution_result(forged_baseline_receipt)

    stable_runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(V4BenchmarkRunner(seed="exact-summary-bindings")),
        tmp_path / "stable",
    )
    stable_result = stable_runner.run(proposals=1)
    assert stable_result.champion_attempt_id == stable_result.attempts[1].attempt_id

    forged_relative = stable_result.model_dump(mode="json")
    relative = forged_relative["attempts"][1]["relative_improvement"]
    forged_relative["attempts"][1]["relative_improvement"] = math.nextafter(relative, math.inf)
    with pytest.raises(ValueError, match="attempt relative improvement does not match"):
        read_kernel_evolution_result(forged_relative)

    forged_attempt_score = stable_result.model_dump(mode="json")
    score = forged_attempt_score["attempts"][1]["score"]
    forged_attempt_score["attempts"][1]["score"] = math.nextafter(score, 0.0)
    with pytest.raises(ValueError, match="attempt score does not match"):
        read_kernel_evolution_result(forged_attempt_score)

    forged_champion_score = stable_result.model_dump(mode="json")
    forged_champion_score["champion_score"] = math.nextafter(stable_result.champion_score, 0.0)
    with pytest.raises(ValueError, match="result champion score does not match"):
        read_kernel_evolution_result(forged_champion_score)

    forged_champion_speedup = stable_result.model_dump(mode="json")
    forged_champion_speedup["champion_speedup_vs_reference"] = math.nextafter(
        stable_result.champion_speedup_vs_reference,
        math.inf,
    )
    with pytest.raises(ValueError, match="result champion speedup does not match"):
        read_kernel_evolution_result(forged_champion_speedup)


@pytest.mark.parametrize(
    ("first_reference", "last_reference", "expected_reason", "rounded_relation"),
    [
        (1.0, 1.03, "significant_improvement", "above"),
        (4.333163826127088, 4.463158740910901, "unstable_environment", "below"),
    ],
)
def test_v4_environment_drift_gate_uses_exact_raw_reference_medians(
    tmp_path: Path,
    first_reference: float,
    last_reference: float,
    expected_reason: str,
    rounded_relation: str,
) -> None:
    benchmark = V4BenchmarkRunner(seed=f"exact-drift-{rounded_relation}")
    original = benchmark.run

    def boundary_drift(
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        if candidate.artifact_digest != incumbent.artifact_digest:
            blocks = execution.report_payload["performance"]["blocks"]
            for block in blocks[:6]:
                block["reference_ms"] = first_reference
            for block in blocks[6:]:
                block["reference_ms"] = last_reference
        return execution

    benchmark.run = boundary_drift  # type: ignore[method-assign]
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            max_environment_drift=0.03,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(benchmark),
        tmp_path,
    )

    result = runner.run(proposals=1)
    attempt = result.attempts[1]
    assert attempt.reason == expected_reason
    assert attempt.observation.environment_drift_ratio is not None
    if rounded_relation == "above":
        assert attempt.observation.environment_drift_ratio > 0.03
    else:
        assert attempt.observation.environment_drift_ratio < 0.03
    assert read_kernel_evolution_result(result.model_dump_json()).verification_status == (
        "v4-finite-sample-policy-replay-verified"
    )


@pytest.mark.parametrize(
    ("device_capacity", "peak", "fraction", "expected_reason"),
    [
        (3, 1, 0.3333333333333333, "memory_limit"),
        (100, 81, 0.80, "memory_limit"),
        (100, 80, 0.80, "significant_improvement"),
    ],
)
def test_v4_memory_fraction_is_exact_and_rejections_remain_replayable(
    tmp_path: Path,
    device_capacity: int,
    peak: int,
    fraction: float,
    expected_reason: str,
) -> None:
    benchmark = V4BenchmarkRunner(seed=f"exact-memory-{device_capacity}-{peak}")
    original = benchmark.run

    def with_memory(
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        candidate_peak = 0 if candidate.artifact_digest == incumbent.artifact_digest else peak
        execution.report_payload["resources"] = {
            "candidate_artifact_digest": candidate.artifact_digest,
            "incumbent_artifact_digest": incumbent.artifact_digest,
            "candidate_peak_allocated_bytes": candidate_peak,
            "candidate_peak_reserved_bytes": candidate_peak,
            "incumbent_peak_allocated_bytes": 0,
            "incumbent_peak_reserved_bytes": 0,
            "device_total_memory_bytes": device_capacity,
        }
        return execution

    benchmark.run = with_memory  # type: ignore[method-assign]
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            max_peak_memory_fraction=fraction,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(benchmark),
        tmp_path,
    )

    result = runner.run(proposals=1)
    assert result.attempts[1].reason == expected_reason
    assert read_kernel_evolution_result(result.model_dump_json()).verification_status == (
        "v4-finite-sample-policy-replay-verified"
    )


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

    forged_bound = report.model_dump(mode="python")
    forged_bound["exact_per_look_bound"] = math.nextafter(report.exact_per_look_bound, math.inf)
    with pytest.raises(ValueError, match="per-look bound disagrees"):
        type(report).model_validate(forged_bound)

    zero_bound = report.model_dump(mode="python")
    zero_bound["exact_per_look_bound"] = 0.0
    with pytest.raises(ValueError, match="greater than 0"):
        type(report).model_validate(zero_bound)

    tiny_alpha_violation = report.model_dump(mode="python")
    tiny_alpha_violation.update(
        {
            "block_count": 64,
            "proposal_cap": 1,
            "familywise_alpha": 2**-65,
            "per_look_alpha": 2**-65,
            "exact_per_look_bound": 2**-64,
            "exact_familywise_bound": 2**-64,
        }
    )
    with pytest.raises(ValueError, match="per-look bound exceeds"):
        type(report).model_validate(tiny_alpha_violation)

    forged_tiny_alpha = report.model_dump(mode="python")
    forged_tiny_alpha.update(
        {
            "block_count": 64,
            "proposal_cap": 2,
            "familywise_alpha": 2**-60,
            "per_look_alpha": 2**-62,
            "exact_per_look_bound": 2**-64,
            "exact_familywise_bound": 2**-63,
        }
    )
    with pytest.raises(ValueError, match="per-look alpha disagrees"):
        type(report).model_validate(forged_tiny_alpha)


def test_confirmation_details_stay_out_of_adaptive_run_dir_until_terminal(tmp_path: Path) -> None:
    primary = _evaluator(
        V4BenchmarkRunner(seed="primary", confirmation_case="private-primary-case-name"),
        adaptive_feedback_policy="aggregate-gates",
    )
    confirmation = _evaluator(
        V4BenchmarkRunner(seed="confirmation", confirmation_case="private-confirmation-case-name")
    )
    public_root = tmp_path / "public"
    sealed_root = tmp_path / "operator-private"
    observed_during_generation: list[str] = []

    def generate(_prompt: str, generation: int) -> str:
        run_dir = next(public_root.iterdir())
        public_text = "\n".join(
            path.read_text(encoding="utf-8") for path in run_dir.rglob("*") if path.is_file()
        )
        observed_during_generation.append(public_text)
        assert "private-primary-case-name" not in public_text
        assert "private-confirmation-case-name" not in public_text
        assert not (run_dir / "reports").exists()
        sealed_text = "\n".join(
            path.read_text(encoding="utf-8") for path in sealed_root.rglob("*") if path.is_file()
        )
        assert "private-primary-case-name" in sealed_text
        if generation == 1:
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
    assert len(released) == 3
    released_text = "\n".join(path.read_text(encoding="utf-8") for path in released)
    assert "private-primary-case-name" in released_text
    assert "private-confirmation-case-name" in released_text
    for path in released:
        audit = json.loads(path.read_text(encoding="utf-8"))
        KernelAttemptRecord.model_validate(audit["attempt"])
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


@pytest.mark.parametrize("target", ["primary", "confirmation"])
def test_v4_complete_report_cannot_be_relabelled_as_arbitrary_ineligible_evidence(
    tmp_path: Path,
    target: str,
) -> None:
    primary = _evaluator(V4BenchmarkRunner(seed=f"relabel-primary-{target}"))
    confirmation = _evaluator(V4BenchmarkRunner(seed=f"relabel-confirmation-{target}"))

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation:
        return _confirmation_observation(confirmation, candidate, incumbent)

    baseline_source = _candidate(1.0).source
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=baseline_source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        primary,
        tmp_path / target / "public",
        confirmation_fn=confirm,
        sealed_audit_root=tmp_path / target / "sealed",
    )
    result = runner.run(proposals=1)
    attempt = result.attempts[1]
    policy = KernelPromotionPolicy(result.decision_policy)
    forged = result.model_dump(mode="json")
    forged_attempt = forged["attempts"][1]

    source_observation = (
        attempt.observation if target == "primary" else attempt.confirmation_observation
    )
    assert source_observation is not None and source_observation.eligible
    observation_payload = source_observation.model_dump(mode="python")
    observation_payload.update(
        eligible=False,
        rejection_reason="forged_operator_veto",
        feedback="attacker-controlled negative evidence",
        derived_statistics_receipt=None,
    )
    forged_observation = KernelBenchmarkObservation.model_validate(observation_payload)
    if target == "primary":
        negative = policy.decide(forged_observation)
        forged_attempt.update(
            observation=forged_observation.model_dump(mode="json"),
            score=None,
            primary_decision=negative.model_dump(mode="json"),
            promotion_decision=negative.model_dump(mode="json"),
            decision=negative.decision,
            reason=negative.reason,
            confirmation_report_digest=None,
            confirmation_observation=None,
            confirmation_decision=None,
        )
    else:
        replayed_observation, negative = evaluate_confirmation_observation(
            observation=forged_observation,
            primary_observation=attempt.observation,
            decide_fn=policy.decide,
            problem_id="finite-sample-kernel",
            protocol_reused=False,
        )
        assert replayed_observation == forged_observation
        final = _confirmation_veto(attempt.primary_decision, negative)
        forged_attempt.update(
            confirmation_observation=forged_observation.model_dump(mode="json"),
            confirmation_decision=negative.model_dump(mode="json"),
            promotion_decision=final.model_dump(mode="json"),
            decision=final.decision,
            reason=final.reason,
        )

    baseline = result.attempts[0]
    forged.update(
        champion_attempt_id=baseline.attempt_id,
        artifact_identity_version=baseline.artifact_identity_version,
        champion_artifact_digest=baseline.artifact_digest,
        champion_source_digest=baseline.source_digest,
        champion_source=baseline_source,
        champion_score=baseline.score,
        champion_speedup_vs_reference=baseline.observation.speedup_vs_reference,
    )
    with pytest.raises(ValueError, match="contradicts its replayable complete report"):
        read_kernel_evolution_result(forged)


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


def _confirmation_observation(
    evaluator: KernelBenchmarkEvaluator,
    candidate: KernelCandidate,
    incumbent: KernelCandidate,
) -> Any:
    baseline = evaluator.evaluate(incumbent, incumbent)
    assert baseline.eligible
    return evaluator.evaluate(
        candidate,
        incumbent,
        expected_scope_id=baseline.hardware_scope_id,
        expected_baseline_id=baseline.baseline_id,
        expected_protocol_id=baseline.protocol_id,
    )


@pytest.mark.parametrize(
    ("outcome", "expected_reason"),
    [
        ("error", "error"),
        ("missing", "missing"),
        ("malformed", "invalid"),
        ("reportless", "command_failed"),
    ],
)
def test_unidentified_confirmation_is_sealed_then_terminates(
    tmp_path: Path,
    outcome: str,
    expected_reason: str,
) -> None:
    marker = "PRIVATE-CONFIRMATION-HOLDOUT-DETAIL"
    primary = _evaluator(
        V4BenchmarkRunner(seed=f"primary-{outcome}"),
        adaptive_feedback_policy="aggregate-gates",
    )
    confirmation = _evaluator(V4BenchmarkRunner(seed=f"confirmation-{outcome}"))
    generated: list[int] = []

    def generate(_prompt: str, generation: int) -> str:
        generated.append(generation)
        return _candidate(0.8 if generation == 0 else 0.7).source

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate):
        observation = _confirmation_observation(confirmation, candidate, incumbent)
        if outcome == "error":
            raise RuntimeError(marker)
        if outcome == "missing":
            return None
        if outcome == "reportless":
            return KernelBenchmarkObservation(
                artifact_identity_version=candidate.artifact_identity_version,
                candidate_artifact_digest=candidate.artifact_digest,
                incumbent_artifact_digest=incumbent.artifact_digest,
                candidate_source_digest=candidate.source_digest,
                incumbent_source_digest=incumbent.source_digest,
                eligible=False,
                rejection_reason="command_failed",
                feedback=marker,
                protocol_id=content_digest("fabricated-unbound-confirmation-id"),
                protocol_compatibility_id=content_digest("fabricated-unbound-confirmation-compatibility"),
            )
        return observation.model_copy(update={"report": "malformed-nested-report"})

    public_root = tmp_path / outcome / "public"
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        generate,
        primary,
        public_root,
        confirmation_fn=confirm,
        sealed_audit_root=tmp_path / outcome / "sealed",
    )

    with pytest.raises(KernelIntegrityError, match="protocol identity is unavailable"):
        runner.run(proposals=2)

    assert generated == [0]
    public_attempt_text = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in (runner.run_dir / "attempts",)
        for path in directory.glob("*.json")
    )
    assert marker not in public_attempt_text
    released = sorted((runner.run_dir / "audit" / "confirmation").glob("*.json"))
    assert len(released) == 2
    audited_attempts = [
        KernelAttemptRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))["attempt"])
        for path in released
    ]
    candidate_audit = next(attempt for attempt in audited_attempts if attempt.role == "candidate")
    if outcome == "reportless":
        assert candidate_audit.confirmation_observation is not None
        assert candidate_audit.confirmation_observation.report is None
    else:
        assert candidate_audit.confirmation_observation is None
    assert candidate_audit.confirmation_decision is not None
    assert candidate_audit.confirmation_decision.reason == expected_reason
    if outcome == "error":
        assert marker in candidate_audit.confirmation_decision.feedback


def test_reused_confirmation_is_schema_valid_and_replays_from_all_exposures(tmp_path: Path) -> None:
    primary = _evaluator(V4BenchmarkRunner(seed="primary-reuse"))
    confirmation = _evaluator(V4BenchmarkRunner(seed="one-confirmation-plan"))

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate):
        return _confirmation_observation(confirmation, candidate, incumbent)

    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, generation: _candidate(0.8 if generation == 0 else 0.64).source,
        primary,
        tmp_path / "public",
        confirmation_fn=confirm,
        sealed_audit_root=tmp_path / "sealed",
    )

    result = runner.run(proposals=2)
    repeated = result.attempts[2]

    assert repeated.reason == "confirmation_not_fresh_across_proposals"
    assert repeated.confirmation_observation is not None
    assert not repeated.confirmation_observation.eligible
    assert repeated.confirmation_observation.rejection_reason == "confirmation_protocol_reused"
    assert repeated.confirmation_observation.derived_statistics_receipt is None
    assert read_kernel_evolution_result(result.model_dump_json()).verification_status == (
        "v4-finite-sample-policy-replay-verified"
    )

    forged_budget = result.model_dump(mode="json")
    evidence = forged_budget["attempts"][1]["sequential_evidence"]
    forged_familywise = math.nextafter(evidence["familywise_alpha"], 0.0)
    forged_per_look = forged_familywise / evidence["proposal_cap"]
    evidence.update(
        familywise_alpha=forged_familywise,
        per_proposal_alpha=forged_per_look,
        cumulative_alpha_spent=forged_per_look * evidence["proposal_index"],
        confidence_level=1.0 - forged_per_look,
    )
    with pytest.raises(ValueError, match="sequential evidence disagrees"):
        read_kernel_evolution_result(forged_budget)

    forged_confidence = result.model_dump(mode="json")
    observation = forged_confidence["attempts"][1]["observation"]
    observation["confidence_level"] = math.nextafter(observation["confidence_level"], 1.0)
    with pytest.raises(ValueError, match="confidence_level disagrees"):
        read_kernel_evolution_result(forged_confidence)

    forged = result.model_dump(mode="json")
    first = forged["attempts"][1]
    primary_decision = first["primary_decision"]
    forged_confirmation = {
        "promote": False,
        "decision": "rejected",
        "reason": "arbitrary_veto",
        "feedback": "forged veto",
        "gates": [],
    }
    first["confirmation_decision"] = forged_confirmation
    first["promotion_decision"] = {
        "promote": False,
        "decision": "rejected",
        "reason": "confirmation_arbitrary_veto",
        "feedback": f'{primary_decision["feedback"]} Independent confirmation veto: forged veto',
        "gates": primary_decision["gates"],
    }
    first["decision"] = "rejected"
    first["reason"] = "confirmation_arbitrary_veto"
    with pytest.raises(ValueError, match="confirmation decision does not replay"):
        read_kernel_evolution_result(forged)


def test_confirmation_plan_commitment_cannot_hide_reuse_behind_a_new_protocol_id(tmp_path: Path) -> None:
    class AlteredProtocolRunner:
        def __init__(self, seed: str) -> None:
            self.inner = V4BenchmarkRunner(seed=seed)

        def manifest(self) -> dict[str, Any]:
            return self.inner.manifest()

        def run(
            self,
            candidate: KernelCandidate,
            incumbent: KernelCandidate,
            *,
            timeout_seconds: float,
        ) -> KernelBenchmarkExecution:
            execution = self.inner.run(candidate, incumbent, timeout_seconds=timeout_seconds)
            assert execution.report_payload is not None
            execution.report_payload["protocol"]["calls_per_block"] = 11
            return execution

    primary = _evaluator(V4BenchmarkRunner(seed="primary-plan-alias"))
    confirmations = iter(
        (
            _evaluator(AlteredProtocolRunner("same-private-plan")),  # type: ignore[arg-type]
            _evaluator(V4BenchmarkRunner(seed="same-private-plan")),
        )
    )
    returned: list[KernelBenchmarkObservation] = []

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation:
        observation = _confirmation_observation(next(confirmations), candidate, incumbent)
        returned.append(observation)
        return observation

    second_source = _candidate(0.79).source
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, generation: _candidate(0.8 if generation == 0 else 0.79).source,
        primary,
        tmp_path / "public",
        confirmation_fn=confirm,
        sealed_audit_root=tmp_path / "sealed",
    )

    result = runner.run(proposals=2)
    first, second = result.attempts[1:]
    assert first.reason == "confirmation_protocol_incompatible"
    assert second.reason == "confirmation_not_fresh_across_proposals"
    assert first.confirmation_observation is not None
    assert second.confirmation_observation is not None
    assert (
        first.confirmation_observation.report.protocol.seed_commitment
        == second.confirmation_observation.report.protocol.seed_commitment
    )
    assert first.confirmation_observation.protocol_id != second.confirmation_observation.protocol_id
    assert read_kernel_evolution_result(result.model_dump_json()).verification_status == (
        "v4-finite-sample-policy-replay-verified"
    )

    original_second = returned[1]
    policy = KernelPromotionPolicy(result.decision_policy)
    replayed, confirmation_decision = evaluate_confirmation_observation(
        observation=original_second,
        primary_observation=second.observation,
        decide_fn=policy.decide,
        problem_id="finite-sample-kernel",
        protocol_reused=False,
    )
    assert replayed == original_second and confirmation_decision.promote
    forged_final = type(second.promotion_decision)(
        promote=True,
        decision="promoted",
        reason=second.primary_decision.reason,
        feedback=(
            f"{second.primary_decision.feedback} Independent fresh confirmation passed all promotion gates."
        ),
        gates=(
            *second.primary_decision.gates,
            *(
                gate.model_copy(update={"name": f"confirmation.{gate.name}"})
                for gate in confirmation_decision.gates
            ),
        ),
    )
    forged = result.model_dump(mode="json")
    forged_attempt = forged["attempts"][2]
    forged_attempt.update(
        confirmation_observation=original_second.model_dump(mode="json"),
        confirmation_decision=confirmation_decision.model_dump(mode="json"),
        promotion_decision=forged_final.model_dump(mode="json"),
        decision="promoted",
        reason=forged_final.reason,
    )
    forged.update(
        champion_attempt_id=second.attempt_id,
        artifact_identity_version=second.artifact_identity_version,
        champion_artifact_digest=second.artifact_digest,
        champion_source_digest=second.source_digest,
        champion_source=second_source,
        champion_score=second.score,
        champion_speedup_vs_reference=second.observation.speedup_vs_reference,
    )
    with pytest.raises(ValueError, match="protocol and plan identities must be unique"):
        read_kernel_evolution_result(forged)


def test_complete_replay_rejects_continuation_after_unidentified_confirmation(tmp_path: Path) -> None:
    primary = _evaluator(V4BenchmarkRunner(seed="primary-unidentified-forgery"))
    first_confirmation_runner = V4BenchmarkRunner(seed="first-known-plan")
    original = first_confirmation_runner.run

    def one_miss(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        execution.report_payload["performance"]["blocks"][-1]["candidate_ms"] = 0.96
        return execution

    first_confirmation_runner.run = one_miss  # type: ignore[method-assign]
    confirmations = iter(
        (
            _evaluator(first_confirmation_runner),
            _evaluator(V4BenchmarkRunner(seed="second-known-plan")),
        )
    )

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate):
        return _confirmation_observation(next(confirmations), candidate, incumbent)

    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, generation: _candidate(0.8 if generation == 0 else 0.75).source,
        primary,
        tmp_path / "public",
        confirmation_fn=confirm,
        sealed_audit_root=tmp_path / "sealed",
    )
    result = runner.run(proposals=2)
    assert result.attempts[1].decision == "rejected"
    assert result.attempts[2].decision == "promoted"

    forged = result.model_dump(mode="json")
    first = forged["attempts"][1]
    primary_decision = first["primary_decision"]
    error_feedback = "Confirmation evaluator failed: RuntimeError: private Gates: confirmation_contract=failed."
    error_gate = {"name": "confirmation_contract", "status": "failed", "detail": ""}
    first["confirmation_observation"] = None
    first["confirmation_report_digest"] = None
    first["confirmation_decision"] = {
        "promote": False,
        "decision": "rejected",
        "reason": "error",
        "feedback": error_feedback,
        "gates": [error_gate],
    }
    first["promotion_decision"] = {
        "promote": False,
        "decision": "rejected",
        "reason": "confirmation_error",
        "feedback": f'{primary_decision["feedback"]} Independent confirmation veto: {error_feedback}',
        "gates": [
            *primary_decision["gates"],
            {"name": "confirmation.confirmation_contract", "status": "failed", "detail": ""},
        ],
    }
    first["decision"] = "rejected"
    first["reason"] = "confirmation_error"

    with pytest.raises(ValueError, match="without report-backed identity"):
        read_kernel_evolution_result(forged)

    reportless = KernelBenchmarkObservation(
        artifact_identity_version=result.attempts[1].artifact_identity_version,
        candidate_artifact_digest=result.attempts[1].artifact_digest,
        incumbent_artifact_digest=result.attempts[1].parent_artifact_digest,
        candidate_source_digest=result.attempts[1].source_digest,
        incumbent_source_digest=result.attempts[1].observation.incumbent_source_digest,
        eligible=False,
        rejection_reason="command_failed",
        feedback="fabricated reportless confirmation identity",
        protocol_id=content_digest("fabricated-reportless-protocol"),
        protocol_compatibility_id=content_digest("fabricated-reportless-compatibility"),
        statistics_policy=result.decision_policy.statistics,
    )
    policy = KernelPromotionPolicy(result.decision_policy)
    reportless_decision = policy.decide(reportless)
    reportless_final = _confirmation_veto(result.attempts[1].primary_decision, reportless_decision)
    forged_reportless = result.model_dump(mode="json")
    forged_first = forged_reportless["attempts"][1]
    forged_first.update(
        confirmation_observation=reportless.model_dump(mode="json"),
        confirmation_report_digest=None,
        confirmation_decision=reportless_decision.model_dump(mode="json"),
        promotion_decision=reportless_final.model_dump(mode="json"),
        decision=reportless_final.decision,
        reason=reportless_final.reason,
    )
    with pytest.raises(ValueError, match="without report-backed identity"):
        read_kernel_evolution_result(forged_reportless)


def test_ineligible_foreign_sequential_protocol_is_persisted_against_host_budget(tmp_path: Path) -> None:
    benchmark = V4BenchmarkRunner(seed="foreign-sequential-protocol")
    original = benchmark.run

    def foreign_cap(candidate: KernelCandidate, incumbent: KernelCandidate, *, timeout_seconds: float):
        execution = original(candidate, incumbent, timeout_seconds=timeout_seconds)
        assert execution.report_payload is not None
        if candidate.artifact_digest != incumbent.artifact_digest:
            execution.report_payload["protocol"]["sequential_testing"]["proposal_cap"] = 9
        return execution

    benchmark.run = foreign_cap  # type: ignore[method-assign]
    public_root = tmp_path / "public"
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(benchmark, adaptive_feedback_policy="aggregate-gates"),
        public_root,
        sealed_audit_root=tmp_path / "sealed",
    )

    result = runner.run(proposals=1)

    assert result.champion_attempt_id == result.baseline_attempt_id
    candidate = result.attempts[1]
    assert candidate.reason == "protocol_mismatch"
    assert candidate.sequential_evidence is not None
    assert candidate.sequential_evidence.proposal_cap == 10
    assert candidate.observation.report is not None
    assert candidate.observation.report.protocol.sequential_testing is not None
    assert candidate.observation.report.protocol.sequential_testing.proposal_cap == 9
    released = sorted((runner.run_dir / "audit" / "confirmation").glob("*.json"))
    assert len(released) == 2
    audited = [
        KernelAttemptRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))["attempt"])
        for path in released
    ]
    assert next(attempt for attempt in audited if attempt.role == "candidate").reason == "protocol_mismatch"


def test_v4_controlled_protocol_baseline_failure_is_audited_and_typed(tmp_path: Path) -> None:
    public_root = tmp_path / "public"
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="finite-sample-kernel",
            task_prompt="Optimize the test kernel.",
            baseline_source=_candidate(1.0).source,
            min_relative_improvement=0.05,
            precision_profile="strict-fp32-v1",
            proposal_cap=10,
            familywise_alpha=0.05,
        ),
        lambda _prompt, _generation: _candidate(0.8).source,
        _evaluator(
            V4BenchmarkRunner(seed="controlled-mismatch"),
            adaptive_feedback_policy="aggregate-gates",
        ),
        public_root,
        sealed_audit_root=tmp_path / "sealed",
    )

    with pytest.raises(KernelBaselineError):
        runner.run(proposals=0)

    public_attempt = json.loads(next((runner.run_dir / "attempts").glob("*.json")).read_text(encoding="utf-8"))
    assert public_attempt["reason"] == "controlled_protocol_mismatch"
    released = list((runner.run_dir / "audit" / "confirmation").glob("*.json"))
    assert len(released) == 1
    audited = KernelAttemptRecord.model_validate(
        json.loads(released[0].read_text(encoding="utf-8"))["attempt"]
    )
    assert not audited.observation.eligible
    assert audited.observation.derived_statistics_receipt is None
