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
    )
    from autocontext.kernel_evolution.protocols import KernelDecisionPolicy


def _close(actual: float | None, expected: float, *, name: str) -> None:
    if actual is None or abs(actual - expected) > 1e-12 * max(1.0, abs(expected)):
        raise ValueError(f"accepted observation {name} does not replay from its raw report")


def _validate_accepted_observation(
    observation: KernelBenchmarkObservation,
    policy: KernelDecisionPolicy,
) -> None:
    """Recompute every promotion-affecting metric from authoritative raw blocks."""
    from autocontext.kernel_evolution.promotion_statistics import (
        bootstrap_lcb,
        geometric_mean_ratio,
        percentile,
    )

    report = observation.report
    if report is None or report.performance is None or report.correctness is None:
        raise ValueError("accepted observations require a complete raw benchmark report")
    from autocontext.kernel_evolution.resource_policy import evaluate_kernel_resource_policy

    resource_result = evaluate_kernel_resource_policy(
        report,
        require_telemetry=policy.statistics.require_resource_telemetry,
        max_gpu_memory_bytes=policy.statistics.max_gpu_memory_bytes,
    )
    if resource_result.reason is not None:
        raise ValueError(f"accepted observation violates its embedded resource policy: {resource_result.reason}")
    candidate_peak = report.resources.candidate_enforced_peak_bytes
    device_capacity = report.resources.device_total_memory_bytes
    if (
        candidate_peak is not None
        and device_capacity is not None
        and candidate_peak > device_capacity * policy.max_peak_memory_fraction
    ):
        raise ValueError("accepted observation exceeds its decision-policy GPU memory fraction")
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
    _close(observation.candidate_median_ms, statistics.median(candidate), name="candidate median")
    _close(observation.incumbent_median_ms, statistics.median(incumbent), name="incumbent median")
    _close(observation.reference_median_ms, statistics.median(reference), name="reference median")
    _close(observation.speedup_vs_incumbent, speedup_incumbent, name="incumbent speedup")
    _close(observation.speedup_vs_reference, speedup_reference, name="reference speedup")
    _close(observation.relative_improvement, 1.0 - (1.0 / speedup_incumbent), name="relative improvement")
    _close(observation.candidate_p95_ms, percentile(candidate, 0.95), name="candidate p95")
    _close(observation.incumbent_p95_ms, percentile(incumbent, 0.95), name="incumbent p95")
    quartile = max(1, len(reference) // 4)
    drift = abs(statistics.median(reference[-quartile:]) / statistics.median(reference[:quartile]) - 1.0)
    _close(observation.environment_drift_ratio, drift, name="environment drift")
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
    expected_cases = all(case.passed_no_regression for case in report.performance.cases) if report.performance.cases else None
    if observation.all_case_no_regression_passed != expected_cases:
        raise ValueError("accepted observation per-case gate does not replay from its raw report")


def _validate_policy_binding(attempt: KernelAttemptRecord) -> None:
    from autocontext.kernel_evolution.confirmation import _confirmation_veto
    from autocontext.kernel_evolution.models import KernelPromotionDecision
    from autocontext.kernel_evolution.runner import KernelPromotionPolicy

    if attempt.schema_version == "autocontext.kernel-lineage/v2":
        return
    if attempt.decision_policy is None or attempt.primary_decision is None or attempt.promotion_decision is None:
        raise ValueError("v3 attempts require complete decision-policy evidence")
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
    if expected_primary.promote:
        _validate_accepted_observation(attempt.observation, attempt.decision_policy)
    if attempt.confirmation_decision is None:
        expected_final = expected_primary
    elif attempt.confirmation_decision.promote:
        confirmation = attempt.confirmation_observation
        if confirmation is None:
            raise ValueError("successful confirmation requires an observation")
        if confirmation.statistics_policy != attempt.decision_policy.statistics:
            raise ValueError("confirmation statistics policy disagrees with the decision policy")
        _validate_accepted_observation(confirmation, attempt.decision_policy)
        expected_confirmation = policy.decide(confirmation)
        if attempt.confirmation_decision != expected_confirmation:
            raise ValueError("successful confirmation decision does not replay under its bound policy")
        expected_final = KernelPromotionDecision(
            promote=True,
            decision="promoted",
            reason=expected_primary.reason,
            feedback=f"{expected_primary.feedback} Independent fresh confirmation passed all promotion gates.",
            gates=(
                *expected_primary.gates,
                *(gate.model_copy(update={"name": f"confirmation.{gate.name}"}) for gate in expected_confirmation.gates),
            ),
        )
    else:
        expected_final = _confirmation_veto(expected_primary, attempt.confirmation_decision)
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

    expected_report_schema = (
        "autocontext.kernelbench-eval/v3"
        if attempt.schema_version == "autocontext.kernel-lineage/v3"
        else "autocontext.kernelbench-eval/v2"
    )
    reports = (
        attempt.observation.report,
        attempt.confirmation_observation.report if attempt.confirmation_observation is not None else None,
    )
    if any(report is not None and report.schema_version != expected_report_schema for report in reports):
        raise ValueError("attempt and embedded benchmark report schemas must use one exact version")
    if attempt.schema_version == "autocontext.kernel-lineage/v2":
        v3_fields = {"decision_policy", "primary_decision", "promotion_decision"}
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
        if report_policy is not None:
            evidence = attempt.sequential_evidence
            if evidence is None:
                raise ValueError("bounded protocol attempts require sequential-testing evidence")
            if (
                evidence.proposal_cap != report_policy.proposal_cap
                or abs(float(evidence.familywise_alpha) - float(report_policy.familywise_alpha)) > 1e-15
            ):
                raise ValueError("attempt sequential evidence disagrees with its protocol")
            if evidence.proposal_index != attempt.generation:
                raise ValueError("attempt sequential proposal index must equal its generation")
    observed_improvement = attempt.observation.relative_improvement
    if (attempt.relative_improvement is None) != (observed_improvement is None) or (
        attempt.relative_improvement is not None
        and observed_improvement is not None
        and abs(float(attempt.relative_improvement) - float(observed_improvement)) > 1e-15
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
            if abs(float(attempt.score) - expected_score) > 1e-15:
                raise ValueError("attempt score does not match its bound decision policy")
    _validate_policy_binding(attempt)


def validate_result(result: KernelEvolutionResult) -> None:
    """Replay the complete champion lineage and bind all result summaries."""
    from autocontext.kernel_evolution.models import artifact_digest_from_source_digest, content_digest

    if not result.attempts:
        raise ValueError("kernel evolution results require at least the baseline attempt")
    expected_attempt_schema = (
        "autocontext.kernel-lineage/v3"
        if result.schema_version == "autocontext.kernel-result/v3"
        else "autocontext.kernel-lineage/v2"
    )
    if any(attempt.schema_version != expected_attempt_schema for attempt in result.attempts):
        raise ValueError("result and attempt schemas must use one exact version")
    if result.schema_version == "autocontext.kernel-result/v2" and "decision_policy" in result.model_fields_set:
        raise ValueError("v2 results cannot contain a v3 decision policy")
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
    successful_confirmation_protocol_ids: set[str] = set()
    for attempt in result.attempts:
        if attempt.run_id != result.run_id:
            raise ValueError("every attempt must belong to the enclosing result run")
        if result.schema_version == "autocontext.kernel-result/v3" and (
            attempt.schema_version != "autocontext.kernel-lineage/v3"
            or result.decision_policy is None
            or attempt.decision_policy != result.decision_policy
        ):
            raise ValueError("v3 results require one exact decision policy across every v3 attempt")
        if attempt.confirmation_decision is not None and attempt.confirmation_decision.promote:
            confirmation = attempt.confirmation_observation
            assert confirmation is not None and confirmation.protocol_id is not None
            if confirmation.protocol_id in successful_confirmation_protocol_ids:
                raise ValueError("successful confirmation protocol ids must be unique across attempts")
            successful_confirmation_protocol_ids.add(confirmation.protocol_id)
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
    if abs(float(result.champion_score) - float(champion.score)) > 1e-15:
        raise ValueError("result champion score does not match the champion attempt")
    observed_speedup = champion.observation.speedup_vs_reference
    if observed_speedup is None or abs(float(result.champion_speedup_vs_reference) - float(observed_speedup)) > 1e-15:
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
