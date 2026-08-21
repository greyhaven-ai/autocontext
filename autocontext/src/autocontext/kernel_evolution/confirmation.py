"""Fresh, audit-only confirmation for adaptive kernel proposals."""

from __future__ import annotations

from collections.abc import Callable

from autocontext.kernel_evolution.models import (
    KernelBenchmarkObservation,
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


def evaluate_confirmation(
    *,
    confirmation_fn: KernelConfirmationFn | None,
    decide_fn: KernelDecisionFn,
    problem_id: str,
    used_protocol_ids: set[str],
    candidate: KernelCandidate,
    incumbent: KernelCandidate,
    primary_observation: KernelBenchmarkObservation,
    provisional: KernelPromotionDecision,
) -> KernelConfirmationResult:
    """Confirm one provisional winner and burn every exposed holdout identity."""
    if confirmation_fn is None or not provisional.promote:
        return None, None, provisional

    def reject(reason: str, feedback: str) -> KernelPromotionDecision:
        return KernelPromotionDecision(
            promote=False,
            decision="rejected",
            reason=reason,
            feedback=f"{feedback} Gates: confirmation_contract=failed.",
            gates=(KernelPromotionGateResult(name="confirmation_contract", status="failed"),),
        )

    try:
        observation = confirmation_fn(candidate, incumbent)
    except Exception as exc:
        confirmation = reject(
            "error",
            f"Confirmation evaluator failed: {type(exc).__name__}: {str(exc)[:1_000]}",
        )
        return None, confirmation, _confirmation_veto(provisional, confirmation)
    if observation is None:
        confirmation = reject("missing", "Confirmation evaluator returned no observation.")
        return None, confirmation, _confirmation_veto(provisional, confirmation)
    if not isinstance(observation, KernelBenchmarkObservation):
        confirmation = reject("invalid", "Confirmation evaluator returned an invalid observation type.")
        return None, confirmation, _confirmation_veto(provisional, confirmation)

    confirmation_protocol_id = observation.protocol_id
    if confirmation_protocol_id is not None:
        if confirmation_protocol_id in used_protocol_ids:
            confirmation = reject(
                "not_fresh_across_proposals",
                "Confirmation reused a protocol identity already exposed by an earlier adaptive proposal.",
            )
            audited_observation = observation.model_copy(
                update={
                    "eligible": False,
                    "rejection_reason": "confirmation_protocol_reused",
                    "feedback": confirmation.feedback,
                }
            )
            return audited_observation, confirmation, _confirmation_veto(provisional, confirmation)
        # Failed and malformed attempts also expose their holdout. Never let a
        # later adaptive proposal reuse that protocol identity.
        used_protocol_ids.add(confirmation_protocol_id)

    report = observation.report
    identity_matches = (
        observation.artifact_identity_version == candidate.artifact_identity_version
        and observation.candidate_artifact_digest == candidate.artifact_digest
        and observation.incumbent_artifact_digest == incumbent.artifact_digest
        and observation.candidate_source_digest == candidate.source_digest
        and observation.incumbent_source_digest == incumbent.source_digest
        and (
            report is None
            or (
                report.artifact_identity_version == candidate.artifact_identity_version
                and report.candidate_artifact_digest == candidate.artifact_digest
                and report.incumbent_artifact_digest == incumbent.artifact_digest
                and report.candidate_source_digest == candidate.source_digest
                and report.incumbent_source_digest == incumbent.source_digest
                and report.candidate_source_suffix == candidate.source_suffix
                and report.incumbent_source_suffix == incumbent.source_suffix
                and report.candidate_entrypoint == candidate.entrypoint
                and report.incumbent_entrypoint == incumbent.entrypoint
            )
        )
    )
    if not identity_matches:
        confirmation = reject(
            "identity_mismatch",
            "Confirmation candidate, incumbent, or entrypoint identity does not match the provisional pair.",
        )
        audited_observation = observation.model_copy(
            update={
                "eligible": False,
                "rejection_reason": "identity_mismatch",
                "feedback": confirmation.feedback,
            }
        )
        return audited_observation, confirmation, _confirmation_veto(provisional, confirmation)
    if report is None:
        confirmation = decide_fn(observation)
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    if report.problem_id != problem_id:
        confirmation = reject("problem_mismatch", "Confirmation used a different kernel problem.")
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    if observation.baseline_id != primary_observation.baseline_id:
        confirmation = reject("baseline_mismatch", "Confirmation used a different reference baseline.")
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    if (
        observation.hardware_scope_id != report.hardware_scope_id
        or observation.baseline_id != report.baseline_id
        or observation.protocol_id != report.protocol.protocol_id
        or observation.protocol_compatibility_id != report.protocol.compatibility_id
    ):
        confirmation = reject("contract_mismatch", "Confirmation observation disagrees with its benchmark report.")
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    primary_report = primary_observation.report
    if primary_report is not None and report.hardware.workload_family_id != primary_report.hardware.workload_family_id:
        confirmation = reject(
            "workload_mismatch",
            "Confirmation changed the static shape, dtype, reference, or input contract.",
        )
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    if primary_report is not None and (
        report.hardware.execution_environment_id != primary_report.hardware.execution_environment_id
    ):
        confirmation = reject(
            "environment_mismatch",
            "Confirmation used a different backend, device, runtime, driver, or toolchain.",
        )
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    if observation.protocol_id == primary_observation.protocol_id:
        confirmation = reject("not_fresh", "Confirmation reused the primary benchmark protocol.")
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    if observation.protocol_compatibility_id != primary_observation.protocol_compatibility_id:
        confirmation = reject(
            "protocol_incompatible",
            "Confirmation changed correctness, tolerance, trial-count, warmup, or timing semantics.",
        )
        return observation, confirmation, _confirmation_veto(provisional, confirmation)

    confirmation = decide_fn(observation)
    if not confirmation.promote:
        return observation, confirmation, _confirmation_veto(provisional, confirmation)
    return (
        observation,
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


__all__ = ["KernelConfirmationFn", "KernelConfirmationResult", "evaluate_confirmation"]
