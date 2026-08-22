"""Fresh, audit-only confirmation for adaptive kernel proposals."""

from __future__ import annotations

from collections.abc import Callable

from autocontext.kernel_evolution.models import (
    KernelBenchmarkObservation,
    KernelBenchmarkReport,
    KernelCandidate,
    KernelPromotionDecision,
    KernelPromotionGateResult,
)

KernelConfirmationFn = Callable[
    [KernelCandidate, KernelCandidate],
    KernelBenchmarkObservation | None,
]
KernelDecisionFn = Callable[[KernelBenchmarkObservation], KernelPromotionDecision]
KernelConfirmationResult = tuple[
    KernelBenchmarkObservation | None,
    KernelPromotionDecision | None,
    KernelPromotionDecision,
]


def _confirmation_rejection(reason: str, feedback: str) -> KernelPromotionDecision:
    return KernelPromotionDecision(
        promote=False,
        decision="rejected",
        reason=reason,
        feedback=f"{feedback} Gates: confirmation_contract=failed.",
        gates=(KernelPromotionGateResult(name="confirmation_contract", status="failed"),),
    )


def _ineligible_confirmation_observation(
    observation: KernelBenchmarkObservation,
    *,
    rejection_reason: str,
    feedback: str,
) -> KernelBenchmarkObservation:
    """Return a validated audit observation after a confirmation contract veto."""
    payload = observation.model_dump(mode="python")
    payload.update(
        {
            "eligible": False,
            "rejection_reason": rejection_reason,
            "feedback": feedback,
            # A finite-sample receipt makes claims only for an eligible
            # observation.  Keeping it on this contract rejection would make
            # the v4 observation internally invalid.
            "derived_statistics_receipt": None,
        }
    )
    return KernelBenchmarkObservation.model_validate(payload)


def _confirmation_veto(
    provisional: KernelPromotionDecision,
    confirmation: KernelPromotionDecision,
) -> KernelPromotionDecision:
    confirmation_gates = tuple(
        gate.model_copy(update={"name": f"confirmation.{gate.name}"}) for gate in confirmation.gates
    )
    return KernelPromotionDecision(
        promote=False,
        decision="rejected",
        reason=f"confirmation_{confirmation.reason}",
        feedback=f"{provisional.feedback} Independent confirmation veto: {confirmation.feedback}",
        gates=(*provisional.gates, *confirmation_gates),
    )


def _confirmation_identity_matches(
    observation: KernelBenchmarkObservation,
    primary_observation: KernelBenchmarkObservation,
) -> bool:
    primary_report = primary_observation.report
    report = observation.report
    return (
        observation.artifact_identity_version == primary_observation.artifact_identity_version
        and observation.candidate_artifact_digest == primary_observation.candidate_artifact_digest
        and observation.incumbent_artifact_digest == primary_observation.incumbent_artifact_digest
        and observation.candidate_source_digest == primary_observation.candidate_source_digest
        and observation.incumbent_source_digest == primary_observation.incumbent_source_digest
        and (
            report is None
            or (
                primary_report is not None
                and report.artifact_identity_version == primary_observation.artifact_identity_version
                and report.candidate_artifact_digest == primary_observation.candidate_artifact_digest
                and report.incumbent_artifact_digest == primary_observation.incumbent_artifact_digest
                and report.candidate_source_digest == primary_observation.candidate_source_digest
                and report.incumbent_source_digest == primary_observation.incumbent_source_digest
                and report.candidate_source_suffix == primary_report.candidate_source_suffix
                and report.incumbent_source_suffix == primary_report.incumbent_source_suffix
                and report.candidate_entrypoint == primary_report.candidate_entrypoint
                and report.incumbent_entrypoint == primary_report.incumbent_entrypoint
            )
        )
    )


def evaluate_confirmation_observation(
    *,
    observation: KernelBenchmarkObservation,
    primary_observation: KernelBenchmarkObservation,
    decide_fn: KernelDecisionFn,
    problem_id: str,
    protocol_reused: bool,
    plan_reused: bool = False,
) -> tuple[KernelBenchmarkObservation, KernelPromotionDecision]:
    """Replay the deterministic contract and policy decision for returned evidence."""
    if protocol_reused or plan_reused:
        confirmation = _confirmation_rejection(
            "not_fresh_across_proposals",
            "Confirmation reused a protocol or plan identity exposed by an earlier adaptive proposal.",
        )
        return (
            _ineligible_confirmation_observation(
                observation,
                rejection_reason="confirmation_protocol_reused",
                feedback=confirmation.feedback,
            ),
            confirmation,
        )

    primary_report = primary_observation.report
    report = observation.report
    identity_rejection = _confirmation_rejection(
        "identity_mismatch",
        "Confirmation candidate, incumbent, or entrypoint identity does not match the provisional pair.",
    )
    identity_matches = _confirmation_identity_matches(observation, primary_observation)
    if not identity_matches:
        confirmation = identity_rejection
        return (
            _ineligible_confirmation_observation(
                observation,
                rejection_reason="identity_mismatch",
                feedback=confirmation.feedback,
            ),
            confirmation,
        )
    if report is None:
        return observation, decide_fn(observation)
    if report.problem_id != problem_id:
        return observation, _confirmation_rejection(
            "problem_mismatch",
            "Confirmation used a different kernel problem.",
        )
    if observation.baseline_id != primary_observation.baseline_id:
        return observation, _confirmation_rejection(
            "baseline_mismatch",
            "Confirmation used a different reference baseline.",
        )
    if (
        observation.hardware_scope_id != report.hardware_scope_id
        or observation.baseline_id != report.baseline_id
        or observation.protocol_id != report.protocol.protocol_id
        or observation.protocol_compatibility_id != report.protocol.compatibility_id
    ):
        return observation, _confirmation_rejection(
            "contract_mismatch",
            "Confirmation observation disagrees with its benchmark report.",
        )
    if primary_report is not None and report.hardware.workload_family_id != primary_report.hardware.workload_family_id:
        return observation, _confirmation_rejection(
            "workload_mismatch",
            "Confirmation changed the static shape, dtype, reference, or input contract.",
        )
    if primary_report is not None and (
        report.hardware.execution_environment_id != primary_report.hardware.execution_environment_id
    ):
        return observation, _confirmation_rejection(
            "environment_mismatch",
            "Confirmation used a different backend, device, runtime, driver, or toolchain.",
        )
    if observation.protocol_id == primary_observation.protocol_id:
        return observation, _confirmation_rejection(
            "not_fresh",
            "Confirmation reused the primary benchmark protocol.",
        )
    if primary_report is not None and report.protocol.seed_commitment == primary_report.protocol.seed_commitment:
        return observation, _confirmation_rejection(
            "not_fresh",
            "Confirmation reused the primary benchmark plan commitment.",
        )
    if observation.protocol_compatibility_id != primary_observation.protocol_compatibility_id:
        return observation, _confirmation_rejection(
            "protocol_incompatible",
            "Confirmation changed correctness, tolerance, trial-count, warmup, or timing semantics.",
        )
    return observation, decide_fn(observation)


def evaluate_confirmation(
    *,
    confirmation_fn: KernelConfirmationFn | None,
    decide_fn: KernelDecisionFn,
    problem_id: str,
    used_evidence_ids: set[str],
    candidate: KernelCandidate,
    incumbent: KernelCandidate,
    primary_observation: KernelBenchmarkObservation,
    provisional: KernelPromotionDecision,
) -> KernelConfirmationResult:
    """Confirm one provisional winner and burn every exposed holdout identity."""
    if confirmation_fn is None or not provisional.promote:
        return None, None, provisional

    try:
        observation = confirmation_fn(candidate, incumbent)
    except Exception as exc:
        confirmation = _confirmation_rejection(
            "error",
            f"Confirmation evaluator failed: {type(exc).__name__}: {str(exc)[:1_000]}",
        )
        return None, confirmation, _confirmation_veto(provisional, confirmation)
    if observation is None:
        confirmation = _confirmation_rejection("missing", "Confirmation evaluator returned no observation.")
        return None, confirmation, _confirmation_veto(provisional, confirmation)
    if not isinstance(observation, KernelBenchmarkObservation):
        confirmation = _confirmation_rejection(
            "invalid",
            "Confirmation evaluator returned an invalid observation type.",
        )
        return None, confirmation, _confirmation_veto(provisional, confirmation)

    try:
        raw_identity_matches: bool | None = _confirmation_identity_matches(
            observation,
            primary_observation,
        )
    except Exception:
        raw_identity_matches = None
    try:
        observation_payload = observation.model_dump(mode="python", warnings="error")
        if observation.report is not None:
            KernelBenchmarkReport.model_validate(observation.report.model_dump(mode="python", warnings="error"))
        if raw_identity_matches:
            observation = KernelBenchmarkObservation.model_validate(observation_payload)
    except Exception:
        confirmation = _confirmation_rejection(
            "invalid",
            "Confirmation evaluator returned an invalid observation type.",
        )
        return None, confirmation, _confirmation_veto(provisional, confirmation)

    protocol_identity = f"protocol:{observation.protocol_id}" if observation.protocol_id is not None else None
    plan_identity = (
        f"plan:{observation.report.protocol.seed_commitment}" if observation.report is not None else None
    )
    protocol_reused = protocol_identity is not None and protocol_identity in used_evidence_ids
    plan_reused = plan_identity is not None and plan_identity in used_evidence_ids
    # Reserve every report-backed identity before inspecting the evidence.  A
    # failed observation still exposed both its compound protocol and private plan.
    used_evidence_ids.update(identity for identity in (protocol_identity, plan_identity) if identity is not None)
    try:
        audited_observation, confirmation = evaluate_confirmation_observation(
            observation=observation,
            primary_observation=primary_observation,
            decide_fn=decide_fn,
            problem_id=problem_id,
            protocol_reused=protocol_reused,
            plan_reused=plan_reused,
        )
    except Exception:
        confirmation = _confirmation_rejection(
            "invalid",
            "Confirmation evaluator returned an invalid observation type.",
        )
        return None, confirmation, _confirmation_veto(provisional, confirmation)
    if not confirmation.promote:
        return audited_observation, confirmation, _confirmation_veto(provisional, confirmation)
    return (
        audited_observation,
        confirmation,
        KernelPromotionDecision(
            promote=True,
            decision="promoted",
            reason=provisional.reason,
            feedback=f"{provisional.feedback} Independent fresh confirmation passed all promotion gates.",
            gates=(
                *provisional.gates,
                *(gate.model_copy(update={"name": f"confirmation.{gate.name}"}) for gate in confirmation.gates),
            ),
        ),
    )


__all__ = [
    "KernelConfirmationFn",
    "KernelConfirmationResult",
    "evaluate_confirmation",
    "evaluate_confirmation_observation",
]
