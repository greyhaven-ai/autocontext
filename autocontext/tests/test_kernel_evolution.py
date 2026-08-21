from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.kernel_evolution import (
    ARTIFACT_IDENTITY_VERSION,
    RELAXED_PRECISION_SEMANTICS,
    SCHEMA_VERSION,
    STRICT_FP32_SEMANTICS,
    KernelBaselineError,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkExecution,
    KernelBenchmarkObservation,
    KernelBenchmarkProtocol,
    KernelBenchmarkReport,
    KernelCandidate,
    KernelCasePerformanceReport,
    KernelCompileReport,
    KernelConfirmationFn,
    KernelCorrectnessReport,
    KernelCorrectnessSliceReport,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelHardwareIdentity,
    KernelIntegrityError,
    KernelPerformanceReport,
    KernelProtocolSemantics,
    KernelSequentialTestingPolicy,
    KernelTimingBlock,
    PrecisionProfileName,
    content_digest,
)


class FakeBenchmarkRunner:
    def __init__(
        self,
        *,
        invalid_baseline: bool = False,
        incorrect_sources: set[str] | None = None,
        seed_commitment: str = "hidden-seeds-v1",
        workload_fingerprint: str = "kernelbench-level1-problem1",
        workload_family: str = "kernelbench-level1-problem1-static-contract",
        architecture: str = "sm90",
        hardware_metadata: dict[str, str] | None = None,
        atol: float = 0.01,
        semantics: KernelProtocolSemantics | None = None,
        sequential_testing: KernelSequentialTestingPolicy | None = None,
        case_regression: bool = False,
    ) -> None:
        self.invalid_baseline = invalid_baseline
        self.incorrect_sources = incorrect_sources or set()
        self.seed_commitment = seed_commitment
        self.atol = atol
        self.semantics = semantics
        self.sequential_testing = sequential_testing
        self.case_regression = case_regression
        self.calls: list[tuple[str, str, float]] = []
        self.latencies = {
            "baseline": 100.0,
            "wrong-fast": 10.0,
            "tiny-gain": 96.0,
            "boundary": 95.0,
            "winner": 90.0,
            "command-fail": 80.0,
            "timeout": 70.0,
            "scope-mismatch": 85.0,
            "protocol-mismatch": 85.0,
            "noisy-margin": 90.0,
        }
        self.hardware = KernelHardwareIdentity(
            backend="fake-cuda",
            architecture=architecture,
            device_name="Fake H100",
            runtime="cuda-12.8",
            driver="580.65",
            toolchain="fake-toolchain-v1",
            workload_family_id=content_digest(workload_family),
            workload_fingerprint=content_digest(workload_fingerprint),
            metadata=hardware_metadata or {},
        )

    def manifest(self) -> dict[str, Any]:
        return {"kind": "fake", "scope": self.hardware.scope_id}

    def run(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        timeout_seconds: float,
    ) -> KernelBenchmarkExecution:
        candidate_name = candidate.source.strip()
        incumbent_name = incumbent.source.strip()
        self.calls.append((candidate_name, incumbent_name, timeout_seconds))
        if candidate_name == "timeout":
            return KernelBenchmarkExecution(returncode=None, timed_out=True)
        if candidate_name == "command-fail":
            return KernelBenchmarkExecution(returncode=17, stderr="compile process crashed")
        if candidate_name == "harness-mutation":
            return KernelBenchmarkExecution(returncode=None, timed_out=True, harness_unchanged=False)

        correct = (
            candidate_name != "wrong-fast"
            and candidate_name not in self.incorrect_sources
            and not (candidate_name == "baseline" and self.invalid_baseline)
        )
        if not correct:
            report = self._report(candidate, incumbent, correct=False)
            return KernelBenchmarkExecution(returncode=0, report_payload=report.model_dump(mode="json"))
        hardware = self.hardware
        if candidate_name == "scope-mismatch":
            hardware = hardware.model_copy(update={"architecture": "sm80"})
        report = self._report(candidate, incumbent, correct=True, hardware=hardware)
        return KernelBenchmarkExecution(returncode=0, report_payload=report.model_dump(mode="json"))

    def _report(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        correct: bool,
        hardware: KernelHardwareIdentity | None = None,
    ) -> KernelBenchmarkReport:
        hardware = hardware or self.hardware
        protocol = KernelBenchmarkProtocol(
            correctness_trials=5,
            hidden_trials=4,
            warmup_runs=3,
            timing_blocks=10,
            calls_per_block=20,
            atol=self.atol,
            rtol=self.atol,
            seed_commitment=content_digest(
                "different-seeds" if candidate.source.strip() == "protocol-mismatch" else self.seed_commitment
            ),
            semantics=self.semantics,
            sequential_testing=self.sequential_testing,
        )
        correctness = KernelCorrectnessReport(
            passed=correct,
            tests_run=5,
            tests_passed=5 if correct else 4,
            hidden_tests_run=4,
            hidden_tests_passed=4 if correct else 3,
            max_abs_error=0.0 if correct else 1.0,
            max_rel_error=0.0 if correct else 1.0,
            failures=[] if correct else ["hidden trial 3 exceeded tolerance"],
            slices=(
                [
                    KernelCorrectnessSliceReport(
                        name=f"case-{index}",
                        split="train" if index == 0 else "holdout",
                        cases_run=1,
                        cases_passed=int(correct or index < 4),
                        passed=correct or index < 4,
                    )
                    for index in range(5)
                ]
                if self.semantics is not None
                else []
            ),
        )
        performance = None
        if correct:
            candidate_ms = self.latencies[candidate.source.strip()]
            incumbent_ms = self.latencies[incumbent.source.strip()]
            candidate_times = [candidate_ms] * 10
            if candidate.source.strip() == "noisy-margin":
                candidate_times = [80.0] * 6 + [110.0] * 4
            performance = KernelPerformanceReport(
                blocks=[
                    KernelTimingBlock(
                        block=block,
                        candidate_ms=timing,
                        incumbent_ms=incumbent_ms,
                        reference_ms=200.0,
                    )
                    for block, timing in enumerate(candidate_times)
                ],
                cases=(
                    [
                        KernelCasePerformanceReport(
                            name=f"case-{index}",
                            split="train" if index == 0 else "holdout",
                            candidate_median_ms=(
                                incumbent_ms / 0.90
                                if self.case_regression and candidate != incumbent and index == 4
                                else candidate_ms
                            ),
                            incumbent_median_ms=incumbent_ms,
                            reference_median_ms=200.0,
                            minimum_speedup_vs_incumbent=0.98,
                            passed_no_regression=not (self.case_regression and candidate != incumbent and index == 4),
                        )
                        for index in range(5)
                    ]
                    if self.semantics is not None
                    else []
                ),
            )
        return KernelBenchmarkReport(
            schema_version=SCHEMA_VERSION,
            evaluation_status="complete" if correct else "candidate_error",
            failure_kind=None if correct else "correctness",
            problem_id="kernelbench-level1-problem1",
            artifact_identity_version=ARTIFACT_IDENTITY_VERSION,
            candidate_artifact_digest=candidate.artifact_digest,
            incumbent_artifact_digest=incumbent.artifact_digest,
            candidate_source_digest=candidate.source_digest,
            incumbent_source_digest=incumbent.source_digest,
            candidate_source_suffix=candidate.source_suffix,
            incumbent_source_suffix=incumbent.source_suffix,
            candidate_entrypoint=candidate.entrypoint,
            incumbent_entrypoint=incumbent.entrypoint,
            baseline_id=content_digest("pytorch-reference-v1"),
            hardware=hardware,
            hardware_scope_id=hardware.scope_id,
            protocol=protocol,
            compile=KernelCompileReport(candidate_passed=True, incumbent_passed=True),
            correctness=correctness,
            performance=performance,
        )


def _evaluator(fake: FakeBenchmarkRunner) -> KernelBenchmarkEvaluator:
    return KernelBenchmarkEvaluator(
        fake,
        KernelBenchmarkEvaluatorConfig(
            problem_id="kernelbench-level1-problem1",
            timeout_seconds=12.5,
            min_timing_blocks=5,
            bootstrap_samples=200,
        ),
    )


def _runner(
    tmp_path: Path,
    fake: FakeBenchmarkRunner,
    proposals: list[str],
    *,
    confirmation_fn: KernelConfirmationFn | None = None,
    precision_profile: PrecisionProfileName | None = None,
    proposal_cap: int | None = None,
) -> tuple[KernelEvolutionRunner, list[str]]:
    prompts: list[str] = []

    def generate(prompt: str, generation: int) -> str:
        prompts.append(prompt)
        return proposals[generation]

    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="kernelbench-level1-problem1",
            task_prompt="Improve ModelNew without changing its ABI. Return only the complete kernel source.",
            baseline_source="baseline",
            min_relative_improvement=0.05,
            target_reference_speedup=3.0,
            precision_profile=precision_profile,
            proposal_cap=proposal_cap,
        ),
        generate,
        _evaluator(fake),
        tmp_path,
        run_id="kernel-test",
        confirmation_fn=confirmation_fn,
    )
    return runner, prompts


def test_v2_artifact_identity_binds_exact_source_bytes_and_framed_abi() -> None:
    original = KernelCandidate(source="same source\n", source_suffix=".py", entrypoint="Model")
    boundary_collision = KernelCandidate(source="same source\n", source_suffix=".p", entrypoint="yModel")
    different_bytes = KernelCandidate(source="same source", source_suffix=".py", entrypoint="Model")

    assert original.artifact_identity_version == ARTIFACT_IDENTITY_VERSION
    assert original.source_digest == boundary_collision.source_digest
    assert original.artifact_digest != boundary_collision.artifact_digest
    assert original.source_digest != different_bytes.source_digest
    assert original.artifact_digest != different_bytes.artifact_digest


def test_protocol_compatibility_excludes_only_the_seed_order_commitment() -> None:
    primary = KernelBenchmarkProtocol(
        correctness_trials=5,
        hidden_trials=4,
        warmup_runs=3,
        timing_blocks=10,
        calls_per_block=20,
        atol=0.01,
        rtol=0.01,
        seed_commitment=content_digest("primary seeds and order"),
    )
    fresh = primary.model_copy(update={"seed_commitment": content_digest("fresh seeds and order")})
    changed_tolerance = fresh.model_copy(update={"atol": 0.02})

    assert primary.protocol_id != fresh.protocol_id
    assert primary.compatibility_id == fresh.compatibility_id
    assert primary.compatibility_id != changed_tolerance.compatibility_id

    historical_h100 = KernelBenchmarkProtocol(
        correctness_trials=5,
        hidden_trials=3,
        warmup_runs=3,
        timing_blocks=8,
        calls_per_block=10,
        atol=0.01,
        rtol=0.01,
        seed_commitment="sha256:6cd3e86b41cc546710198e3f274adfe2e54c636069e5ff0e4ac0c1ea3e50477a",
    )
    assert historical_h100.protocol_id == "sha256:c73d37aa027b0269158718d10bdef9865b8156b7151728e6c2911dac5400c670"


def test_named_precision_semantics_and_private_commitments_are_protocol_bound() -> None:
    sequential = KernelSequentialTestingPolicy(proposal_cap=10, familywise_alpha=0.05)
    primary = KernelBenchmarkProtocol(
        correctness_trials=5,
        hidden_trials=4,
        warmup_runs=3,
        timing_blocks=10,
        calls_per_block=20,
        atol=0.0001,
        rtol=0.0001,
        seed_commitment=content_digest("worker-private primary inputs and order"),
        semantics=STRICT_FP32_SEMANTICS,
        sequential_testing=sequential,
    )
    confirmation = primary.model_copy(update={"seed_commitment": content_digest("disjoint confirmation inputs and order")})
    relaxed = primary.model_copy(
        update={
            "atol": 0.01,
            "rtol": 0.01,
            "semantics": RELAXED_PRECISION_SEMANTICS,
        }
    )

    assert primary.protocol_id != confirmation.protocol_id
    assert primary.compatibility_id == confirmation.compatibility_id
    assert primary.compatibility_id != relaxed.compatibility_id
    assert primary.semantics is not None and primary.semantics.profile_name == "strict-fp32-v1"
    assert relaxed.semantics is not None and relaxed.semantics.profile_name == "relaxed-precision-v1"

    assert primary.semantics is not None
    changed_reference = primary.semantics.model_copy(
        update={
            "reference": primary.semantics.reference.model_copy(update={"tf32_allowed": True}),
        }
    )
    changed_distribution = primary.semantics.model_copy(
        update={
            "inputs": primary.semantics.inputs.model_copy(update={"family": "different-input-family"}),
        }
    )
    changed_enforcement = primary.semantics.model_copy(
        update={
            "enforcement": primary.semantics.enforcement.model_copy(update={"minimum_case_speedup_vs_incumbent": 0.99}),
        }
    )
    for changed in (changed_reference, changed_distribution, changed_enforcement):
        assert primary.compatibility_id != primary.model_copy(update={"semantics": changed}).compatibility_id
    assert primary.compatibility_id != primary.model_copy(update={"atol": 0.001}).compatibility_id
    assert (
        primary.compatibility_id
        != primary.model_copy(update={"sequential_testing": KernelSequentialTestingPolicy(proposal_cap=20)}).compatibility_id
    )


def test_sequential_policy_changes_actual_bound_and_persists_every_proposal(tmp_path: Path) -> None:
    sequential = KernelSequentialTestingPolicy(proposal_cap=3, familywise_alpha=0.05)
    fake = FakeBenchmarkRunner(sequential_testing=sequential)
    evaluator = _evaluator(fake)
    baseline = KernelCandidate(source="baseline")
    noisy = KernelCandidate(source="noisy-margin")
    baseline_observation = evaluator.evaluate(baseline, baseline)
    observation = evaluator.evaluate(noisy, baseline)

    assert baseline_observation.eligible and observation.eligible
    assert observation.confidence_level == pytest.approx(1 - (0.05 / 3))
    assert observation.speedup_lcb is not None and observation.speedup_lcb95 is not None
    assert observation.speedup_lcb < observation.speedup_lcb95

    runner, _ = _runner(
        tmp_path,
        fake,
        ["winner", "tiny-gain", "boundary"],
        proposal_cap=3,
    )
    result = runner.run(proposals=3)
    baseline_record, *proposal_records = result.attempts
    assert baseline_record.sequential_evidence is None
    assert [record.sequential_evidence.proposal_index for record in proposal_records if record.sequential_evidence] == [
        1,
        2,
        3,
    ]
    assert all(
        record.sequential_evidence is not None
        and record.sequential_evidence.per_proposal_alpha == pytest.approx(0.05 / 3)
        and record.sequential_evidence.cumulative_alpha_spent == pytest.approx((0.05 / 3) * index)
        for index, record in enumerate(proposal_records, start=1)
    )


def test_host_owned_proposal_cap_rejects_excess_before_benchmarking(tmp_path: Path) -> None:
    sequential = KernelSequentialTestingPolicy(proposal_cap=2)
    fake = FakeBenchmarkRunner(sequential_testing=sequential)
    runner, _ = _runner(tmp_path, fake, ["winner", "tiny-gain", "boundary"], proposal_cap=2)

    with pytest.raises(ValueError, match="host-owned proposal cap"):
        runner.run(proposals=3)

    assert fake.calls == []


def test_host_owned_profile_mismatch_fails_before_generation(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner(semantics=RELAXED_PRECISION_SEMANTICS)
    runner, prompts = _runner(
        tmp_path,
        fake,
        ["winner"],
        precision_profile="strict-fp32-v1",
    )

    with pytest.raises(KernelBaselineError, match="controlled_protocol_mismatch"):
        runner.run(proposals=1)

    assert prompts == []
    assert len(fake.calls) == 1


def test_semantic_protocol_requires_exact_per_case_coverage() -> None:
    fake = FakeBenchmarkRunner(semantics=STRICT_FP32_SEMANTICS, atol=0.0001)
    candidate = KernelCandidate(source="winner")
    incumbent = KernelCandidate(source="baseline")
    payload = fake._report(candidate, incumbent, correct=True).model_dump(mode="json")
    payload["performance"]["cases"].pop()

    with pytest.raises(ValueError, match="cover every named correctness slice"):
        KernelBenchmarkReport.model_validate(payload)


def test_per_case_no_regression_floor_vetoes_aggregate_winner(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner(semantics=STRICT_FP32_SEMANTICS, case_regression=True, atol=0.0001)
    runner, _ = _runner(
        tmp_path,
        fake,
        ["winner"],
        precision_profile="strict-fp32-v1",
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    assert result.precision_profile == "strict-fp32-v1"
    assert result.attempts[-1].reason == "case_regression"


def test_correctness_first_promotion_and_append_only_lineage(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner()
    slate = ["wrong-fast", "tiny-gain", "winner", "command-fail", "timeout"]
    runner, prompts = _runner(tmp_path, fake, slate)

    result = runner.run(proposals=len(slate))

    assert len(fake.calls) == 6  # baseline plus five proposals
    assert all(call[2] == 12.5 for call in fake.calls)
    assert [call[1] for call in fake.calls] == ["baseline", "baseline", "baseline", "baseline", "winner", "winner"]
    assert result.champion_source == "winner"
    assert result.champion_speedup_vs_reference == pytest.approx(200 / 90)
    assert result.attempts[-1].reason == "timeout"
    assert [record.reason for record in result.attempts] == [
        "baseline",
        "correctness_failed",
        "insufficient_improvement",
        "significant_improvement",
        "command_failed",
        "timeout",
    ]

    baseline, wrong, tiny, winner, command_fail, timeout = result.attempts
    assert baseline.parent_attempt_id is None
    assert wrong.parent_attempt_id == baseline.attempt_id
    assert tiny.parent_attempt_id == baseline.attempt_id
    assert winner.parent_attempt_id == baseline.attempt_id
    assert command_fail.parent_attempt_id == winner.attempt_id
    assert timeout.parent_attempt_id == winner.attempt_id
    assert result.champion_attempt_id == winner.attempt_id

    # The failed correctness result is carried into the next generation prompt.
    assert "correctness failed" in prompts[1].lower()
    run_dir = tmp_path / "kernel-test"
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "champion.py").read_text(encoding="utf-8") == "winner"
    lines = (run_dir / "lineage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6
    assert [json.loads(line)["attempt_id"] for line in lines] == [record.attempt_id for record in result.attempts]
    assert all(record.report_digest is not None for record in result.attempts[:4])
    assert command_fail.report_digest is None
    assert timeout.report_digest is None
    assert all(not record.confirmation_required for record in result.attempts)
    assert all(record.confirmation_observation is None for record in result.attempts)


def test_provisional_winner_is_independently_confirmed_and_promoted(tmp_path: Path) -> None:
    primary = FakeBenchmarkRunner()
    confirmation = FakeBenchmarkRunner(
        seed_commitment="fresh-hidden-seeds-v2",
        workload_fingerprint="kernelbench-level1-problem1-fresh-seeds",
    )
    confirmation_evaluator = _evaluator(confirmation)
    seen_pairs: list[tuple[KernelCandidate, KernelCandidate]] = []

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation:
        seen_pairs.append((candidate, incumbent))
        return confirmation_evaluator.evaluate(candidate, incumbent)

    runner, _ = _runner(tmp_path, primary, ["winner"], confirmation_fn=confirm)

    result = runner.run(proposals=1)

    assert result.champion_source == "winner"
    assert [(candidate.source, incumbent.source) for candidate, incumbent in seen_pairs] == [("winner", "baseline")]
    assert all(candidate.source_suffix == ".py" and candidate.entrypoint == "ModelNew" for candidate, _ in seen_pairs)
    attempt = result.attempts[-1]
    assert attempt.decision == "promoted"
    assert attempt.reason == "significant_improvement"
    assert attempt.confirmation_required
    assert attempt.confirmation_decision is not None and attempt.confirmation_decision.promote
    assert attempt.confirmation_observation is not None
    assert attempt.confirmation_observation.protocol_id != attempt.protocol_id
    assert attempt.confirmation_observation.protocol_compatibility_id == attempt.protocol_compatibility_id
    assert attempt.confirmation_observation.hardware_scope_id != attempt.hardware_scope_id
    assert attempt.confirmation_report_digest is not None
    summary = json.loads((tmp_path / "kernel-test" / "summary.json").read_text(encoding="utf-8"))
    assert summary["attempts"][-1]["confirmation_report_digest"] == attempt.confirmation_report_digest
    assert summary["attempts"][-1]["confirmation_decision"]["promote"] is True


def test_same_protocol_confirmation_fails_freshness_gate(tmp_path: Path) -> None:
    confirmation = FakeBenchmarkRunner()
    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["winner"],
        confirmation_fn=_evaluator(confirmation).evaluate,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_not_fresh"
    assert attempt.confirmation_observation is not None
    assert attempt.confirmation_observation.protocol_id == attempt.protocol_id
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "not_fresh"


def test_semantically_changed_confirmation_fails_compatibility_gate(tmp_path: Path) -> None:
    confirmation = FakeBenchmarkRunner(
        seed_commitment="fresh-hidden-seeds-v2",
        atol=0.02,
    )
    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["winner"],
        confirmation_fn=_evaluator(confirmation).evaluate,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_protocol_incompatible"
    assert attempt.confirmation_observation is not None
    assert attempt.confirmation_observation.protocol_id != attempt.protocol_id
    assert attempt.confirmation_observation.protocol_compatibility_id != attempt.protocol_compatibility_id
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "protocol_incompatible"


def test_confirmation_execution_environment_mismatch_fails_closed(tmp_path: Path) -> None:
    confirmation = FakeBenchmarkRunner(
        seed_commitment="fresh-hidden-seeds-v2",
        architecture="sm80",
    )
    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["winner"],
        confirmation_fn=_evaluator(confirmation).evaluate,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_environment_mismatch"
    assert attempt.confirmation_observation is not None
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "environment_mismatch"


def test_confirmation_hardware_metadata_mismatch_fails_closed(tmp_path: Path) -> None:
    primary = FakeBenchmarkRunner(hardware_metadata={"device_uuid": "GPU-primary"})
    confirmation = FakeBenchmarkRunner(
        seed_commitment="fresh-hidden-seeds-v2",
        hardware_metadata={"device_uuid": "GPU-other"},
    )
    runner, _ = _runner(
        tmp_path,
        primary,
        ["winner"],
        confirmation_fn=_evaluator(confirmation).evaluate,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_environment_mismatch"
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "environment_mismatch"


def test_confirmation_workload_family_mismatch_fails_closed(tmp_path: Path) -> None:
    confirmation = FakeBenchmarkRunner(
        seed_commitment="fresh-hidden-seeds-v2",
        workload_family="different-shape-or-dtype-contract",
    )
    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["winner"],
        confirmation_fn=_evaluator(confirmation).evaluate,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_workload_mismatch"
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "workload_mismatch"


def test_eligible_observation_rejects_report_artifact_identity_mismatch() -> None:
    candidate = KernelCandidate(source="winner")
    incumbent = KernelCandidate(source="baseline")
    observation = _evaluator(FakeBenchmarkRunner()).evaluate(candidate, incumbent)
    payload = observation.model_dump(mode="json")
    payload["candidate_artifact_digest"] = content_digest("different-artifact")

    with pytest.raises(ValueError, match="eligible observation artifact identity"):
        KernelBenchmarkObservation.model_validate(payload)


def test_ineligible_confirmation_rejects_provisional_winner(tmp_path: Path) -> None:
    primary = FakeBenchmarkRunner()
    confirmation = FakeBenchmarkRunner(
        incorrect_sources={"winner"},
        seed_commitment="fresh-hidden-seeds-v2",
    )
    runner, _ = _runner(
        tmp_path,
        primary,
        ["winner"],
        confirmation_fn=_evaluator(confirmation).evaluate,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.observation.eligible
    assert attempt.reason == "confirmation_correctness_failed"
    assert attempt.confirmation_observation is not None
    assert not attempt.confirmation_observation.eligible
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "correctness_failed"


def test_missing_confirmation_rejects_provisional_winner(tmp_path: Path) -> None:
    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["winner"],
        confirmation_fn=lambda _candidate, _incumbent: None,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_missing"
    assert attempt.confirmation_observation is None
    assert attempt.confirmation_report_digest is None
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "missing"


def test_confirmation_is_not_called_for_a_provisional_loser(tmp_path: Path) -> None:
    confirmation = FakeBenchmarkRunner(seed_commitment="fresh-hidden-seeds-v2")
    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["tiny-gain"],
        confirmation_fn=_evaluator(confirmation).evaluate,
    )

    result = runner.run(proposals=1)

    assert confirmation.calls == []
    attempt = result.attempts[-1]
    assert attempt.reason == "insufficient_improvement"
    assert attempt.confirmation_required
    assert attempt.confirmation_observation is None
    assert attempt.confirmation_decision is None


def test_confirmation_artifact_mismatch_fails_closed_and_is_audited(tmp_path: Path) -> None:
    confirmation = _evaluator(FakeBenchmarkRunner(seed_commitment="fresh-hidden-seeds-v2"))

    def mismatch(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation:
        observation = confirmation.evaluate(candidate, incumbent)
        return observation.model_copy(update={"candidate_artifact_digest": content_digest("different-source")})

    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["winner"],
        confirmation_fn=mismatch,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_identity_mismatch"
    assert attempt.confirmation_observation is not None
    assert attempt.confirmation_observation.candidate_artifact_digest == content_digest("different-source")
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "identity_mismatch"


def test_confirmation_entrypoint_mismatch_fails_closed(tmp_path: Path) -> None:
    confirmation = _evaluator(FakeBenchmarkRunner(seed_commitment="fresh-hidden-seeds-v2"))

    def mismatch(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation:
        observation = confirmation.evaluate(candidate, incumbent)
        assert observation.report is not None
        report = observation.report.model_copy(update={"candidate_entrypoint": "DifferentEntrypoint"})
        return observation.model_copy(update={"report": report})

    runner, _ = _runner(
        tmp_path,
        FakeBenchmarkRunner(),
        ["winner"],
        confirmation_fn=mismatch,
    )

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    attempt = result.attempts[-1]
    assert attempt.reason == "confirmation_identity_mismatch"
    assert attempt.confirmation_observation is not None
    assert attempt.confirmation_decision is not None
    assert attempt.confirmation_decision.reason == "identity_mismatch"


def test_exact_improvement_boundary_promotes(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner()
    runner, _ = _runner(tmp_path, fake, ["boundary"])

    result = runner.run(proposals=1)

    assert result.champion_source == "boundary"
    assert result.attempts[-1].decision == "promoted"
    assert result.attempts[-1].relative_improvement == pytest.approx(0.05)


def test_scope_mismatch_is_rejected(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner()
    runner, _ = _runner(tmp_path, fake, ["scope-mismatch"])

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    assert result.attempts[-1].reason == "scope_mismatch"


def test_protocol_mismatch_is_rejected(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner()
    runner, _ = _runner(tmp_path, fake, ["protocol-mismatch"])

    result = runner.run(proposals=1)

    assert result.champion_source == "baseline"
    assert result.attempts[-1].reason == "protocol_mismatch"
    assert result.protocol_id == result.attempts[0].protocol_id


def test_confidence_bound_must_support_the_configured_margin(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner()
    runner, _ = _runner(tmp_path, fake, ["noisy-margin"])

    result = runner.run(proposals=1)

    assert result.attempts[-1].relative_improvement is not None
    assert result.attempts[-1].relative_improvement > 0.05
    assert result.attempts[-1].reason == "confidence_interval"


def test_harness_compromise_is_terminal_and_persisted(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner()
    runner, _ = _runner(tmp_path, fake, ["harness-mutation"])

    with pytest.raises(KernelIntegrityError):
        runner.run(proposals=1)

    run_dir = tmp_path / "kernel-test"
    lines = (run_dir / "lineage.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["reason"] == "harness_modified"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["error_type"] == "KernelIntegrityError"


def test_existing_run_directory_is_rejected_before_modification(tmp_path: Path) -> None:
    first, _ = _runner(tmp_path, FakeBenchmarkRunner(), [])
    first.run(proposals=0)
    lineage_before = (tmp_path / "kernel-test" / "lineage.jsonl").read_bytes()

    with pytest.raises(FileExistsError):
        _runner(tmp_path, FakeBenchmarkRunner(), [])

    assert (tmp_path / "kernel-test" / "lineage.jsonl").read_bytes() == lineage_before


def test_invalid_baseline_is_terminal_but_auditable(tmp_path: Path) -> None:
    fake = FakeBenchmarkRunner(invalid_baseline=True)
    runner, _ = _runner(tmp_path, fake, [])

    with pytest.raises(KernelBaselineError) as raised:
        runner.run(proposals=0)

    assert raised.value.run_dir == tmp_path / "kernel-test"
    assert not (raised.value.run_dir / "champion.py").exists()
    lineage = (raised.value.run_dir / "lineage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lineage) == 1
    assert json.loads(lineage[0])["reason"] == "correctness_failed"


def test_agent_task_custom_promotion_can_reject_first_candidate() -> None:
    from autocontext.execution.agent_task_evolution import AgentTaskEvolutionRunner, AgentTaskGenerationEvaluation

    runner = AgentTaskEvolutionRunner(
        task_prompt="t",
        generate_fn=lambda _prompt, _generation: "candidate",
        evaluate_fn=lambda output, _generation: AgentTaskGenerationEvaluation(
            output=output,
            score=1.0,
            reasoning="invalid for this domain",
        ),
        promotion_fn=lambda _state, _evaluation: False,
    )

    _, state = runner.run_with_state(1)

    assert state.best_output == ""
    assert state.best_score == 0.0


def test_default_agent_task_promotion_behavior_is_unchanged() -> None:
    from autocontext.execution.agent_task_evolution import AgentTaskEvolutionRunner, AgentTaskGenerationEvaluation

    runner = AgentTaskEvolutionRunner(
        task_prompt="t",
        generate_fn=lambda _prompt, _generation: "candidate",
        evaluate_fn=lambda output, _generation: AgentTaskGenerationEvaluation(
            output=output,
            score=0.0,
            reasoning="legacy behavior",
        ),
    )

    _, state = runner.run_with_state(1)

    assert state.best_output == "candidate"


def test_configuration_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        KernelBenchmarkEvaluatorConfig(problem_id="p", timeout_seconds=float("nan"))
    with pytest.raises(ValueError):
        KernelEvolutionConfig(
            problem_id="p",
            task_prompt="t",
            baseline_source="source",
            target_reference_speedup=float("nan"),
        )
