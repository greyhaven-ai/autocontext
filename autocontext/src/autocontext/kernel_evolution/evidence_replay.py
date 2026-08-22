"""Relational replay validation for persisted kernel-evolution evidence."""

from __future__ import annotations

import re
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.kernel_evolution.models import (
        KernelAttemptRecord,
        KernelBenchmarkObservation,
        KernelEvolutionResult,
        KernelPromotionDecision,
    )
    from autocontext.kernel_evolution.protocols import KernelDecisionPolicy


def _close(actual: float | None, expected: float, *, name: str, exact: bool = False) -> None:
    mismatch = actual is None or (
        actual != expected if exact else abs(actual - expected) > 1e-12 * max(1.0, abs(expected))
    )
    if mismatch:
        raise ValueError(f"eligible observation {name} does not replay from its raw report")


def _validate_eligible_observation(
    observation: KernelBenchmarkObservation,
    policy: KernelDecisionPolicy,
) -> None:
    """Recompute every promotion-affecting metric from authoritative raw blocks."""
    from autocontext.kernel_evolution.promotion_statistics import bootstrap_lcb, geometric_mean_ratio, percentile

    report = observation.report
    if report is None or report.performance is None or report.correctness is None:
        raise ValueError("accepted observations require a complete raw benchmark report")
    from autocontext.kernel_evolution.resource_policy import evaluate_kernel_resource_policy

    finite_sample = policy.statistics.schema_version == "autocontext.kernel-statistics-policy/v2"
    resource_result = evaluate_kernel_resource_policy(
        report,
        require_telemetry=policy.statistics.require_resource_telemetry,
        max_gpu_memory_bytes=policy.statistics.max_gpu_memory_bytes,
    )
    if resource_result.reason is not None:
        raise ValueError(f"accepted observation violates its embedded resource policy: {resource_result.reason}")
    if report.protocol.sequential_testing != policy.sequential_testing:
        raise ValueError("accepted observation sequential policy disagrees with its decision policy")
    blocks = report.performance.blocks
    if len(blocks) < policy.statistics.min_timing_blocks:
        raise ValueError("accepted observation has fewer timing blocks than its statistics policy")
    candidate = [float(block.candidate_ms) for block in blocks]
    incumbent = [float(block.incumbent_ms) for block in blocks]
    reference = [float(block.reference_ms) for block in blocks]
    speedup_incumbent = geometric_mean_ratio(incumbent, candidate)
    speedup_reference = geometric_mean_ratio(reference, candidate)
    alpha = policy.sequential_testing.per_proposal_alpha if policy.sequential_testing is not None else 0.05
    seed_material = f"{report.baseline_id}:{report.hardware_scope_id}:{report.protocol.seed_commitment}"
    _close(
        observation.candidate_median_ms,
        statistics.median(candidate),
        name="candidate median",
        exact=finite_sample,
    )
    _close(
        observation.incumbent_median_ms,
        statistics.median(incumbent),
        name="incumbent median",
        exact=finite_sample,
    )
    _close(
        observation.reference_median_ms,
        statistics.median(reference),
        name="reference median",
        exact=finite_sample,
    )
    _close(observation.speedup_vs_incumbent, speedup_incumbent, name="incumbent speedup", exact=finite_sample)
    _close(observation.speedup_vs_reference, speedup_reference, name="reference speedup", exact=finite_sample)
    _close(
        observation.relative_improvement,
        1.0 - (1.0 / speedup_incumbent),
        name="relative improvement",
        exact=finite_sample,
    )
    _close(observation.candidate_p95_ms, percentile(candidate, 0.95), name="candidate p95", exact=finite_sample)
    _close(observation.incumbent_p95_ms, percentile(incumbent, 0.95), name="incumbent p95", exact=finite_sample)
    quartile = max(1, len(reference) // 4)
    drift = abs(statistics.median(reference[-quartile:]) / statistics.median(reference[:quartile]) - 1.0)
    _close(observation.environment_drift_ratio, drift, name="environment drift", exact=finite_sample)
    expected_cases = all(case.passed_no_regression for case in report.performance.cases) if report.performance.cases else None
    if observation.all_case_no_regression_passed != expected_cases:
        raise ValueError("accepted observation per-case gate does not replay from its raw report")
    if finite_sample:
        from autocontext.kernel_evolution.finite_sample import derive_finite_sample_receipt
        from autocontext.kernel_evolution.models import kernel_benchmark_report_digest

        receipt = observation.derived_statistics_receipt
        if receipt is None:
            raise ValueError("v4 eligible observations require a finite-sample derivation receipt")
        replayed = derive_finite_sample_receipt(
            blocks=list(zip(candidate, incumbent, reference, strict=True)),
            statistics_policy=policy.statistics,
            raw_report_digest=kernel_benchmark_report_digest(report),
            schedule_seed_material=report.protocol.seed_commitment,
            per_look_alpha=alpha,
            all_case_no_regression_passed=expected_cases,
        )
        if receipt != replayed:
            raise ValueError("finite-sample derivation receipt does not replay from raw blocks and policy")
        if observation.speedup_lcb95 is not None or observation.speedup_lcb is not None:
            raise ValueError("v4 observations cannot contain empirical bootstrap confidence claims")
    else:
        if policy.statistics.bootstrap_samples is None:
            raise ValueError("v1 statistics policy is missing its bootstrap sample count")
        _close(
            observation.speedup_lcb95,
            bootstrap_lcb(
                list(zip(candidate, incumbent, strict=True)),
                samples=policy.statistics.bootstrap_samples,
                seed_material=seed_material,
                alpha=0.05,
            ),
            name="95% bootstrap lower bound",
        )
        _close(
            observation.speedup_lcb,
            bootstrap_lcb(
                list(zip(candidate, incumbent, strict=True)),
                samples=policy.statistics.bootstrap_samples,
                seed_material=seed_material,
                alpha=alpha,
            ),
            name="sequential bootstrap lower bound",
        )


def _report_visible_rejection_reason(
    observation: KernelBenchmarkObservation,
    policy: KernelDecisionPolicy,
    *,
    expected_problem_id: str | None = None,
    expected_scope_id: str | None = None,
    expected_baseline_id: str | None = None,
    expected_protocol_id: str | None = None,
) -> str | None:
    """Replay evaluator rejections that are provable from a persisted v4 report."""
    from autocontext.kernel_evolution.finite_sample import derive_finite_sample_receipt
    from autocontext.kernel_evolution.models import kernel_benchmark_report_digest
    from autocontext.kernel_evolution.resource_policy import evaluate_kernel_resource_policy

    report = observation.report
    if report is None:
        return None
    if expected_problem_id is not None and report.problem_id != expected_problem_id:
        return "problem_mismatch"
    if (
        report.artifact_identity_version != observation.artifact_identity_version
        or report.candidate_artifact_digest != observation.candidate_artifact_digest
        or report.incumbent_artifact_digest != observation.incumbent_artifact_digest
        or report.candidate_source_digest != observation.candidate_source_digest
        or report.incumbent_source_digest != observation.incumbent_source_digest
    ):
        return "identity_mismatch"
    if expected_scope_id is not None and report.hardware_scope_id != expected_scope_id:
        return "scope_mismatch"
    if expected_baseline_id is not None and report.baseline_id != expected_baseline_id:
        return "baseline_mismatch"
    if expected_protocol_id is not None and report.protocol.protocol_id != expected_protocol_id:
        return "protocol_mismatch"
    if report.failure_kind in {"oom", "timeout"}:
        return report.failure_kind
    if report.evaluation_status == "infrastructure_error":
        return "infrastructure_error"
    if not report.compile.incumbent_passed:
        return "incumbent_failed"
    if not report.compile.candidate_passed:
        return "compile_failed"
    if report.correctness is None or not report.correctness.passed:
        return "correctness_failed"
    if report.evaluation_status != "complete" or report.performance is None:
        return "contract_error"
    timing = report.metadata.get("timing_comparability")
    if isinstance(timing, dict) and (
        timing.get("candidate_incumbent_comparable") is not True
        or timing.get("reference_comparable") is not True
        or timing.get("promotion_comparison") != ["candidate_ms", "incumbent_ms"]
    ):
        return "timing_boundary_mismatch"
    resource_result = evaluate_kernel_resource_policy(
        report,
        require_telemetry=policy.statistics.require_resource_telemetry,
        max_gpu_memory_bytes=policy.statistics.max_gpu_memory_bytes,
    )
    if resource_result.reason is not None:
        return resource_result.reason
    blocks = report.performance.blocks
    if len(blocks) < policy.statistics.min_timing_blocks:
        return "insufficient_samples"
    if report.protocol.sequential_testing != policy.sequential_testing:
        return "protocol_mismatch"
    candidate = [float(block.candidate_ms) for block in blocks]
    incumbent = [float(block.incumbent_ms) for block in blocks]
    reference = [float(block.reference_ms) for block in blocks]
    expected_cases = all(case.passed_no_regression for case in report.performance.cases) if report.performance.cases else None
    alpha = policy.sequential_testing.per_proposal_alpha if policy.sequential_testing is not None else 0.05
    try:
        derive_finite_sample_receipt(
            blocks=list(zip(candidate, incumbent, reference, strict=True)),
            statistics_policy=policy.statistics,
            raw_report_digest=kernel_benchmark_report_digest(report),
            schedule_seed_material=report.protocol.seed_commitment,
            per_look_alpha=alpha,
            all_case_no_regression_passed=expected_cases,
        )
    except (ArithmeticError, ValueError):
        return "contract_error"
    return None


def _validate_ineligible_v4_report(
    observation: KernelBenchmarkObservation,
    policy: KernelDecisionPolicy,
    **expected: str | None,
) -> None:
    """Reject relabeling when a complete report deterministically derives valid evidence."""
    if observation.report is None:
        return
    reason = _report_visible_rejection_reason(observation, policy, **expected)
    if reason is None:
        raise ValueError("ineligible v4 observation contradicts its replayable complete report")
    if observation.rejection_reason != reason:
        raise ValueError("ineligible v4 observation rejection reason does not replay from its report")


def _replay_absent_confirmation_decision(
    confirmation: KernelPromotionDecision,
) -> KernelPromotionDecision:
    from autocontext.kernel_evolution.confirmation import _confirmation_rejection

    if confirmation.reason == "missing":
        return _confirmation_rejection("missing", "Confirmation evaluator returned no observation.")
    if confirmation.reason == "invalid":
        return _confirmation_rejection(
            "invalid",
            "Confirmation evaluator returned an invalid observation type.",
        )
    if confirmation.reason == "error":
        suffix = " Gates: confirmation_contract=failed."
        if not confirmation.feedback.startswith("Confirmation evaluator failed: ") or not confirmation.feedback.endswith(
            suffix
        ):
            raise ValueError("confirmation error decision does not match the fail-closed contract")
        detail = confirmation.feedback[: -len(suffix)]
        return _confirmation_rejection("error", detail)
    raise ValueError("confirmation without an observation has an invalid decision reason")


def _replay_confirmation_outcome(
    attempt: KernelAttemptRecord,
    *,
    protocol_reused: bool,
    plan_reused: bool = False,
    validate_ineligible_report: bool = False,
) -> KernelPromotionDecision:
    from autocontext.kernel_evolution.confirmation import (
        _confirmation_veto,
        evaluate_confirmation_observation,
    )
    from autocontext.kernel_evolution.models import KernelPromotionDecision
    from autocontext.kernel_evolution.runner import KernelPromotionPolicy

    assert attempt.decision_policy is not None
    assert attempt.primary_decision is not None
    assert attempt.confirmation_decision is not None
    policy = KernelPromotionPolicy(attempt.decision_policy)
    confirmation_observation = attempt.confirmation_observation
    if confirmation_observation is None:
        expected_confirmation = _replay_absent_confirmation_decision(attempt.confirmation_decision)
    else:
        primary_report = attempt.observation.report
        if primary_report is None:
            raise ValueError("confirmation evidence requires a primary benchmark report")
        replayed_observation, expected_confirmation = evaluate_confirmation_observation(
            observation=confirmation_observation,
            primary_observation=attempt.observation,
            decide_fn=policy.decide,
            problem_id=primary_report.problem_id,
            protocol_reused=protocol_reused,
            plan_reused=plan_reused,
        )
        if confirmation_observation != replayed_observation:
            raise ValueError("confirmation audit observation does not replay from its contract")
        if (
            validate_ineligible_report
            and not confirmation_observation.eligible
            and expected_confirmation == policy.decide(confirmation_observation)
        ):
            _validate_ineligible_v4_report(confirmation_observation, attempt.decision_policy)
    if attempt.confirmation_decision != expected_confirmation:
        raise ValueError("confirmation decision does not replay from its observation and contract")
    if expected_confirmation.promote:
        return KernelPromotionDecision(
            promote=True,
            decision="promoted",
            reason=attempt.primary_decision.reason,
            feedback=(
                f"{attempt.primary_decision.feedback} Independent fresh confirmation passed all promotion gates."
            ),
            gates=(
                *attempt.primary_decision.gates,
                *(
                    gate.model_copy(update={"name": f"confirmation.{gate.name}"})
                    for gate in expected_confirmation.gates
                ),
            ),
        )
    return _confirmation_veto(attempt.primary_decision, expected_confirmation)


def _validate_policy_binding(
    attempt: KernelAttemptRecord,
    *,
    complete_result: bool = False,
    expected_problem_id: str | None = None,
    expected_scope_id: str | None = None,
    expected_baseline_id: str | None = None,
    expected_protocol_id: str | None = None,
) -> None:
    from autocontext.kernel_evolution.runner import KernelPromotionPolicy

    if attempt.schema_version == "autocontext.kernel-lineage/v2":
        return
    if attempt.decision_policy is None or attempt.primary_decision is None or attempt.promotion_decision is None:
        raise ValueError("v3 attempts require complete decision-policy evidence")
    if attempt.schema_version == "autocontext.kernel-lineage/v4":
        if (
            attempt.decision_policy.schema_version != "autocontext.kernel-decision-policy/v2"
            or attempt.decision_policy_id != attempt.decision_policy.policy_id
        ):
            raise ValueError("v4 attempts require the exact canonical decision-policy digest")
    elif attempt.decision_policy_id is not None and attempt.decision_policy_id != attempt.decision_policy.policy_id:
        raise ValueError("v3 attempt contains an ambiguous decision-policy digest")
    if attempt.role == "baseline":
        if attempt.confirmation_required:
            raise ValueError("baseline attempts cannot require confirmation")
    elif attempt.confirmation_required != attempt.decision_policy.require_confirmation:
        raise ValueError("v3 candidate confirmation requirement disagrees with its decision policy")
    policy = KernelPromotionPolicy(attempt.decision_policy)
    expected_primary = policy.decide(attempt.observation, baseline=attempt.role == "baseline")
    if attempt.primary_decision != expected_primary:
        raise ValueError("attempt primary decision does not replay under its bound policy")
    if attempt.confirmation_decision is not None and not expected_primary.promote:
        raise ValueError("confirmation evidence requires a provisionally promotable primary decision")
    if attempt.observation.statistics_policy != attempt.decision_policy.statistics:
        raise ValueError("attempt observation statistics policy disagrees with its decision policy")
    if attempt.observation.eligible:
        _validate_eligible_observation(attempt.observation, attempt.decision_policy)
    elif complete_result and attempt.schema_version == "autocontext.kernel-lineage/v4":
        _validate_ineligible_v4_report(
            attempt.observation,
            attempt.decision_policy,
            expected_problem_id=expected_problem_id,
            expected_scope_id=expected_scope_id,
            expected_baseline_id=expected_baseline_id,
            expected_protocol_id=expected_protocol_id,
        )
    confirmation = attempt.confirmation_observation
    if confirmation is not None and confirmation.eligible:
        if confirmation.statistics_policy != attempt.decision_policy.statistics:
            raise ValueError("confirmation statistics policy disagrees with the decision policy")
        _validate_eligible_observation(confirmation, attempt.decision_policy)
    elif (
        complete_result
        and confirmation is not None
        and confirmation.report is not None
        and confirmation.statistics_policy != attempt.decision_policy.statistics
    ):
        raise ValueError("confirmation statistics policy disagrees with the decision policy")
    if attempt.confirmation_decision is None:
        expected_final = expected_primary
    else:
        # Cross-attempt freshness is checked against actual history in
        # validate_result.  The per-attempt validator can still replay every
        # other contract gate and the complete negative policy decision.
        expected_final = _replay_confirmation_outcome(
            attempt,
            protocol_reused=attempt.confirmation_decision.reason == "not_fresh_across_proposals",
            validate_ineligible_report=complete_result,
        )
    if attempt.promotion_decision != expected_final:
        raise ValueError("attempt final decision does not replay from primary and confirmation evidence")
    if attempt.decision != expected_final.decision or attempt.reason != expected_final.reason:
        raise ValueError("attempt disposition does not match its replayed promotion decision")


def validate_attempt(attempt: KernelAttemptRecord) -> None:
    """Validate one attempt independently of its enclosing lineage graph."""
    from autocontext.kernel_evolution.models import (
        artifact_digest_from_source_digest,
        kernel_benchmark_report_digest,
    )

    expected_report_schema = {
        "autocontext.kernel-lineage/v2": "autocontext.kernelbench-eval/v2",
        "autocontext.kernel-lineage/v3": "autocontext.kernelbench-eval/v3",
        "autocontext.kernel-lineage/v4": "autocontext.kernelbench-eval/v4",
    }[attempt.schema_version]
    reports = (
        attempt.observation.report,
        attempt.confirmation_observation.report if attempt.confirmation_observation is not None else None,
    )
    if any(report is not None and report.schema_version != expected_report_schema for report in reports):
        raise ValueError("attempt and embedded benchmark report schemas must use one exact version")
    if attempt.schema_version == "autocontext.kernel-lineage/v2":
        v3_fields = {"decision_policy", "decision_policy_id", "primary_decision", "promotion_decision"}
        if v3_fields & attempt.model_fields_set:
            raise ValueError("v2 attempts cannot contain v3 decision-policy fields")
    if attempt.artifact_identity_version != attempt.observation.artifact_identity_version:
        raise ValueError("attempt artifact identity version does not match its observation")
    if attempt.source_digest != attempt.observation.candidate_source_digest:
        raise ValueError("attempt source digest does not match its observation")
    if attempt.artifact_digest != artifact_digest_from_source_digest(
        attempt.source_digest,
        source_suffix=attempt.source_suffix,
        entrypoint=attempt.entrypoint,
    ):
        raise ValueError("attempt artifact digest does not match its source digest and ABI")
    if attempt.artifact_digest != attempt.observation.candidate_artifact_digest:
        raise ValueError("attempt artifact digest does not match its observation")
    for name in ("hardware_scope_id", "baseline_id", "protocol_id", "protocol_compatibility_id"):
        if getattr(attempt, name) != getattr(attempt.observation, name):
            raise ValueError(f"attempt {name.replace('_', ' ')} does not match its observation")
    if (attempt.report_digest is None) != (attempt.observation.report is None):
        raise ValueError("attempt report digest presence does not match its observation")
    if attempt.observation.report is not None and attempt.report_digest != kernel_benchmark_report_digest(
        attempt.observation.report
    ):
        raise ValueError("attempt report digest does not match its embedded report")
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", attempt.source_suffix):
        raise ValueError("attempt source_suffix is invalid")
    if not attempt.entrypoint.strip():
        raise ValueError("attempt entrypoint must not be empty")
    if not attempt.confirmation_required and (
        attempt.confirmation_report_digest is not None
        or attempt.confirmation_observation is not None
        or attempt.confirmation_decision is not None
    ):
        raise ValueError("confirmation evidence requires confirmation_required")
    confirmation = attempt.confirmation_observation
    if confirmation is None:
        if attempt.confirmation_report_digest is not None:
            raise ValueError("confirmation report digest requires a confirmation observation")
    elif (attempt.confirmation_report_digest is None) != (confirmation.report is None):
        raise ValueError("confirmation report digest presence does not match its observation")
    if (
        confirmation is not None
        and confirmation.report is not None
        and attempt.confirmation_report_digest != kernel_benchmark_report_digest(confirmation.report)
    ):
        raise ValueError("confirmation report digest does not match its embedded report")
    if confirmation is not None and attempt.confirmation_decision is None:
        raise ValueError("confirmation observations require a confirmation decision")
    if attempt.confirmation_decision is not None and attempt.confirmation_decision.promote:
        if attempt.confirmation_decision.decision != "promoted":
            raise ValueError("successful confirmation must use a promoted disposition")
        if confirmation is None:
            raise ValueError("successful confirmation requires an observation")
        if not confirmation.eligible:
            raise ValueError("successful confirmation requires an eligible observation")
        if confirmation.candidate_artifact_digest != attempt.artifact_digest:
            raise ValueError("successful confirmation candidate digest does not match the attempt")
        if confirmation.baseline_id != attempt.baseline_id:
            raise ValueError("successful confirmation baseline does not match the primary observation")
        if confirmation.protocol_id == attempt.protocol_id:
            raise ValueError("successful confirmation must use a fresh benchmark protocol")
        if confirmation.protocol_compatibility_id != attempt.protocol_compatibility_id:
            raise ValueError("successful confirmation must use a compatible benchmark protocol")
        primary_report = attempt.observation.report
        confirmation_report = confirmation.report
        if primary_report is None or confirmation_report is None:
            raise ValueError("successful confirmation requires primary and confirmation reports")
        if confirmation_report.problem_id != primary_report.problem_id:
            raise ValueError("successful confirmation problem does not match the primary report")
        if confirmation_report.hardware.workload_family_id != primary_report.hardware.workload_family_id:
            raise ValueError("successful confirmation workload family does not match the primary report")
        if (
            confirmation_report.candidate_artifact_digest != attempt.artifact_digest
            or confirmation_report.incumbent_artifact_digest != confirmation.incumbent_artifact_digest
        ):
            raise ValueError("successful confirmation report artifact identity does not match its observation")
        if (
            confirmation_report.candidate_entrypoint != attempt.entrypoint
            or confirmation_report.incumbent_entrypoint != primary_report.incumbent_entrypoint
        ):
            raise ValueError("successful confirmation entrypoint identity does not match the primary report")
        if confirmation_report.hardware.execution_environment_id != primary_report.hardware.execution_environment_id:
            raise ValueError("successful confirmation execution environment does not match the primary report")
        if attempt.decision != "promoted":
            raise ValueError("successful confirmation requires a promoted attempt decision")
    if attempt.decision == "promoted" and attempt.confirmation_required:
        if attempt.confirmation_decision is None or not attempt.confirmation_decision.promote:
            raise ValueError("promoted attempts requiring confirmation must contain a successful confirmation")
    if attempt.decision in {"baseline", "promoted"} and not attempt.observation.eligible:
        raise ValueError("accepted attempts require an eligible primary observation")
    if attempt.role == "baseline":
        if attempt.confirmation_required:
            raise ValueError("baseline attempts cannot require confirmation")
        if attempt.sequential_evidence is not None:
            raise ValueError("baseline attempts cannot spend a proposal alpha budget")
        if attempt.generation != 0 or attempt.parent_attempt_id is not None or attempt.parent_artifact_digest is not None:
            raise ValueError("baseline attempts must be generation zero lineage roots")
        if attempt.decision not in {"baseline", "rejected"}:
            raise ValueError("baseline attempts must establish the baseline or be rejected")
        if attempt.observation.incumbent_artifact_digest != attempt.artifact_digest:
            raise ValueError("baseline must be evaluated against itself")
    else:
        if attempt.generation < 1 or attempt.parent_attempt_id is None or attempt.parent_artifact_digest is None:
            raise ValueError("candidate attempts require a champion parent")
        if attempt.decision == "baseline":
            raise ValueError("candidate attempts cannot carry a baseline decision")
        if attempt.parent_artifact_digest != attempt.observation.incumbent_artifact_digest:
            raise ValueError("candidate parent digest must match the paired incumbent")
        if attempt.confirmation_decision is not None and attempt.confirmation_decision.promote:
            assert confirmation is not None
            if confirmation.incumbent_artifact_digest != attempt.parent_artifact_digest:
                raise ValueError("successful confirmation incumbent digest does not match the candidate parent")
        report_policy = attempt.observation.report.protocol.sequential_testing if attempt.observation.report else None
        receipt_policy = (
            attempt.decision_policy.sequential_testing if attempt.decision_policy is not None else report_policy
        )
        evidence = attempt.sequential_evidence
        if receipt_policy is None:
            if evidence is not None:
                raise ValueError("unbounded protocol attempts cannot contain sequential-testing evidence")
        else:
            if evidence is None:
                raise ValueError("bounded decision-policy attempts require sequential-testing evidence")
            if (
                evidence.proposal_cap != receipt_policy.proposal_cap
                or float(evidence.familywise_alpha) != float(receipt_policy.familywise_alpha)
            ):
                raise ValueError("attempt sequential evidence disagrees with its decision policy")
            if evidence.proposal_index != attempt.generation:
                raise ValueError("attempt sequential proposal index must equal its generation")
        if attempt.observation.eligible and report_policy != receipt_policy:
            raise ValueError("accepted observation sequential policy disagrees with its decision policy")
    observed_improvement = attempt.observation.relative_improvement
    exact_summaries = attempt.schema_version == "autocontext.kernel-lineage/v4"
    if (attempt.relative_improvement is None) != (observed_improvement is None) or (
        attempt.relative_improvement is not None
        and observed_improvement is not None
        and (
            float(attempt.relative_improvement) != float(observed_improvement)
            if exact_summaries
            else abs(float(attempt.relative_improvement) - float(observed_improvement)) > 1e-15
        )
    ):
        raise ValueError("attempt relative improvement does not match its observation")
    if not attempt.observation.eligible and attempt.score is not None:
        raise ValueError("ineligible observations cannot carry an attempt score")
    if attempt.observation.eligible:
        assert attempt.observation.speedup_vs_reference is not None
        if attempt.score is None:
            raise ValueError("eligible observations require an attempt score")
        if attempt.decision_policy is not None:
            expected_score = min(
                1.0,
                float(attempt.observation.speedup_vs_reference) / float(attempt.decision_policy.target_reference_speedup),
            )
            score_mismatch = (
                float(attempt.score) != expected_score
                if exact_summaries
                else abs(float(attempt.score) - expected_score) > 1e-15
            )
            if score_mismatch:
                raise ValueError("attempt score does not match its bound decision policy")
    _validate_policy_binding(attempt)


def validate_result(result: KernelEvolutionResult) -> None:
    """Replay the complete champion lineage and bind all result summaries."""
    from autocontext.kernel_evolution.models import artifact_digest_from_source_digest, content_digest

    if not result.attempts:
        raise ValueError("kernel evolution results require at least the baseline attempt")
    expected_attempt_schema = {
        "autocontext.kernel-result/v2": "autocontext.kernel-lineage/v2",
        "autocontext.kernel-result/v3": "autocontext.kernel-lineage/v3",
        "autocontext.kernel-result/v4": "autocontext.kernel-lineage/v4",
    }[result.schema_version]
    if any(attempt.schema_version != expected_attempt_schema for attempt in result.attempts):
        raise ValueError("result and attempt schemas must use one exact version")
    if result.schema_version == "autocontext.kernel-result/v2" and {
        "decision_policy",
        "decision_policy_id",
    } & result.model_fields_set:
        raise ValueError("v2 results cannot contain a newer decision policy")
    if result.schema_version == "autocontext.kernel-result/v4":
        if (
            result.decision_policy is None
            or result.decision_policy.schema_version != "autocontext.kernel-decision-policy/v2"
            or result.decision_policy_id != result.decision_policy.policy_id
        ):
            raise ValueError("v4 results require the exact canonical decision-policy digest")
    elif result.schema_version == "autocontext.kernel-result/v3" and result.decision_policy_id is not None:
        if result.decision_policy is None or result.decision_policy_id != result.decision_policy.policy_id:
            raise ValueError("v3 result contains an ambiguous decision-policy digest")
    by_id = {attempt.attempt_id: attempt for attempt in result.attempts}
    if len(by_id) != len(result.attempts):
        raise ValueError("attempt ids must be unique")
    generations = [attempt.generation for attempt in result.attempts]
    if generations != list(range(len(result.attempts))):
        raise ValueError("attempt generations must be unique, ordered, and contiguous from zero")
    baseline = by_id.get(result.baseline_attempt_id)
    if baseline is None or baseline is not result.attempts[0] or baseline.role != "baseline" or baseline.decision != "baseline":
        raise ValueError("baseline_attempt_id must identify the baseline root")
    current = baseline
    exposed_confirmation_evidence_ids: set[str] = set()
    for attempt in result.attempts:
        if attempt.run_id != result.run_id:
            raise ValueError("every attempt must belong to the enclosing result run")
        if result.schema_version in {"autocontext.kernel-result/v3", "autocontext.kernel-result/v4"} and (
            attempt.schema_version != expected_attempt_schema
            or result.decision_policy is None
            or attempt.decision_policy != result.decision_policy
        ):
            raise ValueError("verified results require one exact decision policy across every attempt")
        _validate_policy_binding(
            attempt,
            complete_result=True,
            expected_problem_id=result.problem_id,
            expected_scope_id=result.hardware_scope_id,
            expected_baseline_id=result.baseline_id,
            expected_protocol_id=result.protocol_id,
        )
        confirmation = attempt.confirmation_observation
        if (
            result.schema_version == "autocontext.kernel-result/v4"
            and attempt.confirmation_decision is not None
            and (confirmation is None or confirmation.report is None or confirmation.protocol_id is None)
        ):
            raise ValueError(
                "complete v4 results cannot continue after a confirmation without report-backed identity"
            )
        if confirmation is not None and confirmation.report is not None and confirmation.protocol_id is not None:
            protocol_identity = f"protocol:{confirmation.protocol_id}"
            plan_identity = f"plan:{confirmation.report.protocol.seed_commitment}"
            protocol_reused = protocol_identity in exposed_confirmation_evidence_ids
            plan_reused = plan_identity in exposed_confirmation_evidence_ids
            if attempt.confirmation_decision is None:
                raise ValueError("exposed confirmation evidence requires a decision")
            if (protocol_reused or plan_reused) and confirmation.rejection_reason != "confirmation_protocol_reused":
                raise ValueError(
                    "confirmation protocol and plan identities must be unique unless repeated exposure is rejected"
                )
            expected_final = _replay_confirmation_outcome(
                attempt,
                protocol_reused=protocol_reused,
                plan_reused=plan_reused,
                validate_ineligible_report=True,
            )
            if attempt.promotion_decision != expected_final:
                raise ValueError("confirmation outcome does not replay from exposed protocol history")
            exposed_confirmation_evidence_ids.update((protocol_identity, plan_identity))
        if attempt.observation.eligible:
            report = attempt.observation.report
            if report is None or report.problem_id != result.problem_id:
                raise ValueError("eligible primary attempt problem id does not match its result")
            if (
                attempt.hardware_scope_id != result.hardware_scope_id
                or attempt.baseline_id != result.baseline_id
                or attempt.protocol_id != result.protocol_id
                or attempt.protocol_compatibility_id != result.protocol_compatibility_id
            ):
                raise ValueError("eligible primary attempt is outside the result's pinned benchmark identities")
        if attempt is baseline:
            continue
        if attempt.parent_attempt_id != current.attempt_id or attempt.parent_artifact_digest != current.artifact_digest:
            raise ValueError("candidate attempt parent must identify the champion at that generation")
        if attempt.decision == "promoted":
            current = attempt
    champion = by_id.get(result.champion_attempt_id)
    if champion is None or champion is not current or champion.decision not in {"baseline", "promoted"}:
        raise ValueError("champion_attempt_id must identify the replayed final champion")
    if not champion.observation.eligible or champion.score is None:
        raise ValueError("champion attempt must contain an eligible scored observation")
    if champion.artifact_digest != result.champion_artifact_digest:
        raise ValueError("champion artifact digest does not match the champion attempt")
    if content_digest(result.champion_source) != result.champion_source_digest:
        raise ValueError("champion source does not match its source digest")
    if champion.source_digest != result.champion_source_digest:
        raise ValueError("champion source digest does not match the champion attempt")
    if result.artifact_identity_version != champion.artifact_identity_version:
        raise ValueError("result artifact identity version does not match the champion attempt")
    exact_summaries = result.schema_version == "autocontext.kernel-result/v4"
    champion_score_mismatch = (
        float(result.champion_score) != float(champion.score)
        if exact_summaries
        else abs(float(result.champion_score) - float(champion.score)) > 1e-15
    )
    if champion_score_mismatch:
        raise ValueError("result champion score does not match the champion attempt")
    observed_speedup = champion.observation.speedup_vs_reference
    speedup_mismatch = observed_speedup is None or (
        float(result.champion_speedup_vs_reference) != float(observed_speedup)
        if exact_summaries
        else abs(float(result.champion_speedup_vs_reference) - float(observed_speedup)) > 1e-15
    )
    if speedup_mismatch:
        raise ValueError("result champion speedup does not match the champion observation")
    expected_champion = artifact_digest_from_source_digest(
        result.champion_source_digest,
        source_suffix=champion.source_suffix,
        entrypoint=champion.entrypoint,
    )
    if expected_champion != result.champion_artifact_digest:
        raise ValueError("champion source and ABI do not match its artifact digest")
    if baseline.hardware_scope_id != result.hardware_scope_id or baseline.baseline_id != result.baseline_id:
        raise ValueError("result scope or baseline does not match the baseline attempt")
    if baseline.protocol_id != result.protocol_id:
        raise ValueError("result protocol does not match the baseline attempt")
    if baseline.protocol_compatibility_id != result.protocol_compatibility_id:
        raise ValueError("result protocol compatibility does not match the baseline attempt")
    baseline_profile = (
        baseline.observation.report.protocol.semantics.profile_name
        if baseline.observation.report is not None and baseline.observation.report.protocol.semantics is not None
        else None
    )
    if result.precision_profile != baseline_profile:
        raise ValueError("result precision profile does not match the baseline protocol")
    if champion.observation.report is None or champion.observation.report.problem_id != result.problem_id:
        raise ValueError("result problem id does not match the champion report")
    if (
        champion.hardware_scope_id != result.hardware_scope_id
        or champion.baseline_id != result.baseline_id
        or champion.protocol_id != result.protocol_id
        or champion.protocol_compatibility_id != result.protocol_compatibility_id
    ):
        raise ValueError("champion was not evaluated in the result's pinned benchmark scope")
