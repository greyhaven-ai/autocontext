"""Replay and aggregate validation for multi-workload kernel studies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autocontext.kernel_evolution._generation_replay import (
    revalidated_generation_record,
    validate_generation_replay,
)
from autocontext.kernel_evolution.models import canonical_digest
from autocontext.kernel_evolution.workload_study_index import validate_measured_evidence_index
from autocontext.kernel_evolution.workload_study_rules import (
    observation_conclusively_failed as _observation_conclusively_failed,
)
from autocontext.kernel_evolution.workload_study_rules import (
    timing_boundaries_comparable,
    validate_observation_policy,
    validate_reportless_observation,
    validate_run_phase_metadata,
    validate_spec_reservations,
    validate_transfer_phase_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from autocontext.kernel_evolution.models import KernelBenchmarkObservation, KernelEvolutionResult
    from autocontext.kernel_evolution.workload_study import (
        KernelChampionTransferAssessment,
        KernelProtocolBurn,
        KernelStudyDimension,
        KernelTransferDisposition,
        KernelTransferEvidence,
        KernelTransferPhaseEvidence,
        KernelWorkloadDisposition,
        KernelWorkloadPhaseEvidence,
        KernelWorkloadRunEvidence,
        KernelWorkloadSpec,
        KernelWorkloadStudyProvenance,
        KernelWorkloadStudyReport,
    )


def _validate_budget_state(run: KernelWorkloadRunEvidence, spec: KernelWorkloadSpec) -> None:
    state = run.budget_state
    budget = spec.budget.generation_budget
    ceilings = (
        (state.input_tokens, budget.max_total_input_tokens, "input-token"),
        (state.output_tokens, budget.max_total_output_tokens, "output-token"),
        (state.total_tokens, budget.max_total_tokens, "token"),
        (float(state.cost_usd), float(budget.max_cost_usd), "cost"),
        (float(state.wall_seconds), float(budget.max_wall_seconds), "generation wall-clock"),
    )
    for actual, maximum, name in ceilings:
        if actual > maximum:
            raise ValueError(f"workload run {run.workload_id!r} exceeded its {name} budget")


def _validate_generation_activity(run: KernelWorkloadRunEvidence, spec: KernelWorkloadSpec) -> None:
    results = tuple(revalidated_generation_record(item) for item in run.generation_results)
    failures = tuple(revalidated_generation_record(item) for item in run.generation_failures)
    providers = tuple(item.provider for item in results) + tuple(item.provider for item in failures)
    validate_generation_replay(
        results,
        failures,
        spec.budget.generation_budget,
        expected_provider=providers[0] if providers else None,
    )


def validate_observation_report_identity(observation: KernelBenchmarkObservation) -> None:
    report = observation.report
    if report is not None and (
        observation.artifact_identity_version != report.artifact_identity_version
        or observation.candidate_artifact_digest != report.candidate_artifact_digest
        or observation.incumbent_artifact_digest != report.incumbent_artifact_digest
        or observation.candidate_source_digest != report.candidate_source_digest
        or observation.incumbent_source_digest != report.incumbent_source_digest
    ):
        raise ValueError("benchmark observation artifact identity does not match its raw report")


def _validate_benchmark_requirements(
    observation: KernelBenchmarkObservation,
    spec: KernelWorkloadSpec,
    *,
    context: str,
) -> None:
    report = observation.report
    if report is None:
        if observation.eligible:
            raise ValueError(f"eligible {context} evidence requires complete correctness and performance")
        return
    if observation.eligible and (report.correctness is None or report.performance is None):
        raise ValueError(f"eligible {context} evidence requires complete correctness and performance")
    required_cases = set(spec.required_benchmark_cases)
    required_slice_counts = {
        split: sum(case.startswith(f"{split}:") for case in required_cases) for split in spec.required_correctness_slices
    }
    if report.correctness is not None:
        slices = report.correctness.slices
        names = [item.name for item in slices]
        by_name = {item.name: item for item in slices}
        if len(names) != len(set(names)) or not set(spec.required_correctness_slices) <= set(names):
            raise ValueError(f"{context} evidence omitted or duplicated required correctness slices")
        if (
            report.correctness.tests_run != len(required_cases)
            or report.correctness.hidden_tests_run != required_slice_counts.get("holdout", 0)
            or any(
                by_name[name].split != name or by_name[name].cases_run != count for name, count in required_slice_counts.items()
            )
        ):
            raise ValueError(f"{context} correctness case coverage disagrees with its workload contract")
    if report.performance is not None:
        cases = [f"{case.split}:{case.name}" for case in report.performance.cases]
        if len(cases) != len(set(cases)) or set(cases) != required_cases:
            raise ValueError(f"{context} performance case coverage disagrees with its workload contract")
        if any(
            float(case.minimum_speedup_vs_incumbent) != float(spec.minimum_case_speedup_vs_incumbent)
            for case in report.performance.cases
        ):
            raise ValueError(f"{context} evidence used the wrong per-case no-regression floor")


def _validate_campaign_report(
    run: KernelWorkloadRunEvidence,
    spec: KernelWorkloadSpec,
    observation: KernelBenchmarkObservation,
) -> None:
    report = observation.report
    if report is None:
        raise ValueError("campaign report validation requires report-backed evidence")
    validate_observation_report_identity(observation)
    _validate_benchmark_requirements(observation, spec, context="campaign")
    shape_profile_id = report.metadata.get("shape_profile_id")
    if (
        report.problem_id != spec.problem_id
        or report.baseline_id != spec.reference_id
        or report.protocol.protocol_id != spec.primary_protocol.protocol_id
        or report.protocol.compatibility_id != spec.protocol_compatibility_id
        or report.protocol.seed_commitment != spec.primary_protocol.plan_commitment
        or report.hardware.workload_family_id != spec.workload_family_id
        or report.hardware.execution_environment_id != spec.execution_environment_id
        or report.hardware_scope_id != spec.primary_protocol.hardware_scope_id
        or shape_profile_id != spec.shape_profile_id
    ):
        raise ValueError(f"workload run {run.workload_id!r} contains campaign evidence for another contract")

def validate_run_against_spec(run: KernelWorkloadRunEvidence, spec: KernelWorkloadSpec) -> None:
    if run.workload_id != spec.workload_id or run.workload_spec_id != spec.spec_id or run.workload_family != spec.workload_family:
        raise ValueError(f"workload run {run.workload_id!r} is bound to a different specification")
    if run.budget_id != spec.budget.budget_id or run.generation_budget_id != spec.budget.generation_budget.budget_id:
        raise ValueError(f"workload run {run.workload_id!r} is bound to a different generation budget")
    if run.proposals_requested > spec.budget.proposal_cap:
        raise ValueError(f"workload run {run.workload_id!r} exceeded its proposal budget")
    _validate_budget_state(run, spec)
    _validate_generation_activity(run, spec)
    validate_spec_reservations(spec)
    result = run.result
    if result.decision_policy != spec.decision_policy:
        raise ValueError(f"workload run {run.workload_id!r} changed its precommitted decision policy")
    if result.problem_id != spec.problem_id or result.baseline_id != spec.reference_id:
        raise ValueError(f"workload run {run.workload_id!r} has the wrong problem or reference identity")
    if result.protocol_id != spec.primary_protocol.protocol_id:
        raise ValueError(f"workload run {run.workload_id!r} has the wrong campaign primary protocol")
    if result.protocol_compatibility_id != spec.protocol_compatibility_id:
        raise ValueError(f"workload run {run.workload_id!r} has an incompatible campaign protocol")
    baseline = result.attempts[0]
    baseline_report = baseline.observation.report
    if baseline_report is None:
        raise ValueError("workload result baseline requires report-backed identity")
    _validate_campaign_report(run, spec, baseline.observation)
    if (
        baseline.artifact_digest != spec.reference_artifact_digest
        or baseline.source_digest != spec.reference_source_digest
        or baseline.source_suffix != spec.reference_source_suffix
        or baseline.entrypoint != spec.reference_entrypoint
        or baseline_report.candidate_artifact_digest != spec.reference_artifact_digest
        or baseline_report.candidate_source_digest != spec.reference_source_digest
        or result.hardware_scope_id != baseline_report.hardware_scope_id
    ):
        raise ValueError("workload result baseline does not match the pinned study contract")
    confirmation_index = 0
    for attempt in result.attempts:
        validate_reportless_observation(attempt.observation)
        validate_observation_policy(attempt.observation, result)
        if attempt.confirmation_observation is not None:
            validate_reportless_observation(attempt.confirmation_observation)
            validate_observation_policy(attempt.confirmation_observation, result)
        report = attempt.observation.report
        if report is not None:
            _validate_campaign_report(run, spec, attempt.observation)
        if attempt.confirmation_decision is None:
            continue
        if confirmation_index >= len(spec.confirmation_protocols):
            raise ValueError("campaign consumed more confirmation plans than were precommitted")
        reservation = spec.confirmation_protocols[confirmation_index]
        confirmation_index += 1
        observation = attempt.confirmation_observation
        if observation is None or observation.report is None:
            continue
        confirmation_report = observation.report
        validate_observation_report_identity(observation)
        _validate_benchmark_requirements(observation, spec, context="campaign confirmation")
        if (
            observation.candidate_artifact_digest != attempt.artifact_digest
            or observation.candidate_source_digest != attempt.source_digest
            or observation.incumbent_artifact_digest != attempt.observation.incumbent_artifact_digest
            or observation.incumbent_source_digest != attempt.observation.incumbent_source_digest
        ):
            raise ValueError("campaign confirmation does not measure its candidate and incumbent pair")
        if (
            observation.protocol_id != reservation.protocol_id
            or confirmation_report.problem_id != spec.problem_id
            or confirmation_report.baseline_id != spec.reference_id
            or confirmation_report.protocol.compatibility_id != spec.protocol_compatibility_id
            or confirmation_report.protocol.seed_commitment != reservation.plan_commitment
            or confirmation_report.hardware.workload_family_id != spec.workload_family_id
            or confirmation_report.hardware.execution_environment_id != spec.execution_environment_id
            or confirmation_report.hardware_scope_id != reservation.hardware_scope_id
            or confirmation_report.metadata.get("shape_profile_id") != spec.shape_profile_id
        ):
            raise ValueError("campaign confirmation did not use its next precommitted reservation")
    for phase, reservation in (
        (run.primary, spec.final_primary_protocol),
        (run.confirmation, spec.final_confirmation_protocol),
    ):
        if phase.reservation != reservation:
            raise ValueError("final workload phase used an unreserved protocol")
        if (
            phase.incumbent_artifact_digest != spec.reference_artifact_digest
            or phase.incumbent_source_digest != spec.reference_source_digest
        ):
            raise ValueError("final workload phase used the wrong incumbent reference artifact")
        validate_observation_policy(phase.observation, result)
        if phase.observation.report is None:
            continue
        if (
            phase.problem_id != spec.problem_id
            or phase.workload_family_id != spec.workload_family_id
            or phase.shape_profile_id != spec.shape_profile_id
            or phase.baseline_id != spec.reference_id
            or phase.protocol_compatibility_id != spec.protocol_compatibility_id
            or phase.execution_environment_id != spec.execution_environment_id
        ):
            raise ValueError("final workload phase is bound to the wrong benchmark identity")
        if phase.observation.report.metadata.get("generation_receipt_context_digest") != (
            run.generation_receipt_context_digest
        ):
            raise ValueError("final workload evidence is bound to different generation receipts")
        _validate_benchmark_requirements(phase.observation, spec, context="final workload phase")
    validate_run_phase_metadata(run, spec)
    expected = workload_disposition(
        result,
        completed_proposals=len(run.generation_results),
        has_terminal_failures=bool(run.generation_failures),
        proposal_cap=spec.budget.proposal_cap,
    )
    if run.disposition != expected:
        raise ValueError("workload disposition disagrees with promotion and completed proposal exhaustion")
    if float(run.total_wall_seconds) > float(spec.budget.max_workload_wall_seconds):
        raise ValueError(f"workload run {run.workload_id!r} exceeded its end-to-end wall-clock budget")
    if float(run.total_cost_usd) > float(spec.budget.max_workload_cost_usd):
        raise ValueError(f"workload run {run.workload_id!r} exceeded its end-to-end cost budget")

def _phase_conclusively_failed(phase: KernelWorkloadPhaseEvidence | KernelTransferPhaseEvidence) -> bool:
    return _observation_conclusively_failed(phase.observation)


def _campaign_evidence_complete(result: KernelEvolutionResult) -> bool:
    attempts = (item for item in result.attempts if item.role == "candidate")
    for attempt in attempts:
        if not (attempt.observation.eligible or _observation_conclusively_failed(attempt.observation)):
            return False
        provisional = attempt.primary_decision is not None and attempt.primary_decision.promote
        confirmation = attempt.confirmation_observation
        if provisional and (
            confirmation is None or not (confirmation.eligible or _observation_conclusively_failed(confirmation))
        ):
            return False
    return True


def workload_disposition(
    result: KernelEvolutionResult,
    *,
    completed_proposals: int,
    has_terminal_failures: bool,
    proposal_cap: int,
) -> KernelWorkloadDisposition:
    promoted = any(item.attempt_id == result.champion_attempt_id and item.decision == "promoted" for item in result.attempts)
    exhausted = completed_proposals == proposal_cap and not has_terminal_failures
    if promoted:
        return "promoted"
    return "plateau" if exhausted and _campaign_evidence_complete(result) else "incomplete"


def assess_champion(
    run: KernelWorkloadRunEvidence,
    *,
    workload_ids: tuple[str, ...],
    required_dimensions: set[KernelStudyDimension],
    transfers: tuple[KernelTransferEvidence, ...],
) -> KernelChampionTransferAssessment:
    from autocontext.kernel_evolution.workload_study import KernelChampionTransferAssessment

    relevant = tuple(item for item in transfers if item.source_workload_id == run.workload_id)
    passed = {run.workload_id} if run.independently_verified else set()
    source_failed = any(_phase_conclusively_failed(phase) for phase in (run.primary, run.confirmation))
    failed = {run.workload_id} if source_failed else set()
    dimensions: set[KernelStudyDimension] = set()
    for transfer in relevant:
        if transfer.passed:
            if transfer.target_workload_id != run.workload_id or run.independently_verified:
                passed.add(transfer.target_workload_id)
            dimensions.update(transfer.dimensions)
        elif any(_phase_conclusively_failed(phase) for phase in (transfer.primary, transfer.confirmation)):
            failed.add(transfer.target_workload_id)
    passed -= failed
    untested = set(workload_ids) - passed - failed
    if run.independently_verified and not untested and not failed and required_dimensions <= dimensions:
        disposition: KernelTransferDisposition = "portable"
    elif not run.independently_verified:
        disposition = "specialist" if source_failed else "unconfirmed"
    elif len(passed) > 1:
        disposition = "cross-workload"
    elif failed:
        disposition = "specialist"
    else:
        disposition = "unconfirmed"
    return KernelChampionTransferAssessment(
        candidate_artifact_digest=run.champion_artifact_digest,
        source_workload_id=run.workload_id,
        passed_workload_ids=tuple(sorted(passed)),
        failed_workload_ids=tuple(sorted(failed)),
        untested_workload_ids=tuple(sorted(untested)),
        covered_dimensions=tuple(sorted(dimensions)),
        disposition=disposition,
    )


def protocol_burns(
    specs: tuple[KernelWorkloadSpec, ...],
    runs: tuple[KernelWorkloadRunEvidence, ...],
    transfers: tuple[KernelTransferEvidence, ...],
) -> tuple[KernelProtocolBurn, ...]:
    from autocontext.kernel_evolution.workload_study import KernelProtocolBurn

    burns: list[KernelProtocolBurn] = []
    spec_by_id = {spec.workload_id: spec for spec in specs}
    for run in runs:
        spec = spec_by_id[run.workload_id]
        baseline_report = run.result.attempts[0].observation.report
        if baseline_report is None:
            raise ValueError("campaign primary burn requires report-backed identity")
        burns.append(
            KernelProtocolBurn(
                workload_id=run.workload_id,
                evidence_id=run.result.baseline_attempt_id,
                kind="campaign-primary",
                protocol_id=run.result.protocol_id,
                plan_commitment=baseline_report.protocol.seed_commitment,
            )
        )
        confirmation_index = 0
        for attempt in run.result.attempts:
            if attempt.confirmation_decision is None:
                continue
            reservation = spec.confirmation_protocols[confirmation_index]
            confirmation_index += 1
            burns.append(
                KernelProtocolBurn(
                    workload_id=run.workload_id,
                    evidence_id=attempt.attempt_id,
                    kind="campaign-confirmation",
                    protocol_id=reservation.protocol_id,
                    plan_commitment=reservation.plan_commitment,
                )
            )
        burns.extend(
            (
                KernelProtocolBurn(
                    workload_id=run.workload_id,
                    evidence_id=run.run_id,
                    kind="final-primary",
                    protocol_id=run.primary.protocol_id,
                    plan_commitment=run.primary.plan_commitment,
                ),
                KernelProtocolBurn(
                    workload_id=run.workload_id,
                    evidence_id=run.run_id,
                    kind="final-confirmation",
                    protocol_id=run.confirmation.protocol_id,
                    plan_commitment=run.confirmation.plan_commitment,
                ),
            )
        )
    for index, transfer in enumerate(transfers):
        evidence_id = f"{index}:{transfer.source_workload_id}->{transfer.target_workload_id}"
        burns.extend(
            (
                KernelProtocolBurn(
                    workload_id=transfer.target_workload_id,
                    evidence_id=evidence_id,
                    kind="transfer-primary",
                    protocol_id=transfer.primary.protocol_id,
                    plan_commitment=transfer.primary.plan_commitment,
                ),
                KernelProtocolBurn(
                    workload_id=transfer.target_workload_id,
                    evidence_id=evidence_id,
                    kind="transfer-confirmation",
                    protocol_id=transfer.confirmation.protocol_id,
                    plan_commitment=transfer.confirmation.plan_commitment,
                ),
            )
        )
    protocol_ids = [item.protocol_id for item in burns]
    plan_ids = [item.plan_commitment for item in burns]
    if len(set(protocol_ids)) != len(protocol_ids) or len(set(plan_ids)) != len(plan_ids):
        raise ValueError("study reused a burned protocol or private plan")
    return tuple(burns)

def _iter_observations(report: KernelWorkloadStudyReport) -> Iterator[KernelBenchmarkObservation]:
    for run in report.workload_runs:
        for attempt in run.result.attempts:
            if attempt.observation.report is not None:
                yield attempt.observation
            if attempt.confirmation_observation is not None and attempt.confirmation_observation.report is not None:
                yield attempt.confirmation_observation
        for run_phase in (run.primary, run.confirmation):
            if run_phase.observation.report is not None:
                yield run_phase.observation
    for transfer in report.transfers:
        for transfer_phase in (transfer.primary, transfer.confirmation):
            if transfer_phase.observation.report is not None:
                yield transfer_phase.observation

def _trusted_elapsed_seconds(observation: KernelBenchmarkObservation) -> float:
    report = observation.report
    if report is None:
        return 0.0
    receipt = report.evaluator_authority_receipt
    assert receipt is not None
    return receipt.transcript.total_elapsed_ns / 1_000_000_000


def _validate_measured_wall_usage(report: KernelWorkloadStudyReport) -> None:
    for run in report.workload_runs:
        campaign_elapsed = sum(
            _trusted_elapsed_seconds(observation)
            for attempt in run.result.attempts
            for observation in (attempt.observation, attempt.confirmation_observation)
            if observation is not None
        )
        if float(run.runner_wall_seconds) < float(run.budget_state.wall_seconds) + campaign_elapsed:
            raise ValueError("measured runner wall usage understates authenticated generation and campaign elapsed time")
        for phase in (run.primary, run.confirmation):
            if float(phase.evaluation_wall_seconds) < _trusted_elapsed_seconds(phase.observation):
                raise ValueError("measured final phase wall usage understates its authenticated evaluator receipt")
    for transfer in report.transfers:
        for transfer_phase in (transfer.primary, transfer.confirmation):
            if float(transfer_phase.evaluation_wall_seconds) < _trusted_elapsed_seconds(transfer_phase.observation):
                raise ValueError("measured transfer wall usage understates its authenticated evaluator receipt")

def _validate_provenance(
    report: KernelWorkloadStudyReport,
    provenance: KernelWorkloadStudyProvenance,
    *,
    validation_context: object,
) -> None:
    if provenance.evidence_kind == "measured" and (
        provenance.warning is not None or "synthetic" in provenance.backend_identity.casefold()
    ):
        raise ValueError("measured study provenance cannot carry synthetic backend markers or warnings")
    trust: dict[str, object] | None = None
    if isinstance(validation_context, dict):
        candidate = validation_context.get("kernel_evaluator_trust")
        trust = candidate if isinstance(candidate, dict) else None
    if provenance.evidence_kind == "measured" and trust is None:
        raise ValueError("measured study evidence requires an explicit external evaluator trust root")
    if provenance.evidence_kind == "measured" and trust is not None and (
        trust.get("evidence_index_digest") != provenance.evidence_index_digest
    ):
        raise ValueError("measured study trust root must pin the exact evidence index digest")
    if provenance.evidence_kind == "measured" and trust is not None:
        validate_measured_evidence_index(report, trust)
    if provenance.evidence_kind == "measured" and any(
        run.primary.observation.report is None and run.confirmation.observation.report is None
        for run in report.workload_runs
    ):
        raise ValueError("measured workload generation context requires a signed final evaluator report")
    expected = {
        "evidence_origin": provenance.evidence_kind,
        "evidence_warning": provenance.warning,
        "study_execution_id": provenance.study_execution_id,
        "study_manifest_digest": provenance.manifest_digest,
        "study_contract_digest": provenance.contract_digest,
        "study_backend_identity": provenance.backend_identity,
    }
    measured_transcripts: set[str] = set()
    measured_receipts: set[str] = set()
    for observation in _iter_observations(report):
        benchmark_report = observation.report
        assert benchmark_report is not None
        for metadata in (benchmark_report.metadata, benchmark_report.hardware.metadata):
            if any(metadata.get(name) != value for name, value in expected.items()):
                raise ValueError("embedded benchmark report provenance disagrees with study provenance")
        if benchmark_report.metadata.get("workload_specs_digest") != provenance.workload_specs_digest:
            raise ValueError("embedded benchmark report is bound to different workload specifications")
        if provenance.evidence_kind == "measured" and (
            benchmark_report.evaluator_authority_receipt is None
            or benchmark_report.resources.telemetry_authority != "trusted-evaluator-observed/v1"
            or "synthetic" in benchmark_report.hardware.backend.casefold()
            or "synthetic" in benchmark_report.hardware.toolchain.casefold()
        ):
            raise ValueError("measured study evidence requires trusted non-synthetic evaluator receipts")
        if provenance.evidence_kind == "measured" and observation.eligible and not timing_boundaries_comparable(
            benchmark_report, require_evidence=True
        ):
            raise ValueError("eligible measured evidence requires trusted timing-boundary comparability")
        if provenance.evidence_kind == "measured":
            from autocontext.kernel_evolution.authority_protocol import verify_authority_receipt

            assert benchmark_report.evaluator_authority_receipt is not None and trust is not None
            receipt = benchmark_report.evaluator_authority_receipt
            if receipt.transcript_digest in measured_transcripts or receipt.receipt_digest in measured_receipts:
                raise ValueError("measured study reused an authenticated evaluator execution")
            measured_transcripts.add(receipt.transcript_digest)
            measured_receipts.add(receipt.receipt_digest)
            key_id = trust.get("key_id")
            secret = trust.get("secret")
            evaluator_build_digest = trust.get("evaluator_build_digest")
            boundary_manifest_digest = trust.get("boundary_manifest_digest")
            if not isinstance(key_id, str) or not isinstance(secret, bytes):
                raise ValueError("measured study evaluator trust root is malformed")
            if not isinstance(evaluator_build_digest, str) or not isinstance(boundary_manifest_digest, str):
                raise ValueError("measured study trust root must pin evaluator build and boundary digests")
            verify_authority_receipt(
                receipt,
                benchmark_report.model_dump(mode="json"),
                trusted_key_id=key_id,
                trusted_secret=secret,
                expected_evaluator_build_digest=evaluator_build_digest,
                expected_boundary_manifest_digest=boundary_manifest_digest,
            )
    if provenance.evidence_kind == "measured":
        _validate_measured_wall_usage(report)


def _validate_transfer(
    transfer: KernelTransferEvidence,
    *,
    spec_by_id: dict[str, KernelWorkloadSpec],
    run_by_id: dict[str, KernelWorkloadRunEvidence],
) -> None:
    if transfer.source_workload_id not in spec_by_id or transfer.target_workload_id not in spec_by_id:
        raise ValueError("transfer evidence references a workload outside the study")
    source_run = run_by_id[transfer.source_workload_id]
    target_run = run_by_id[transfer.target_workload_id]
    source_spec = spec_by_id[transfer.source_workload_id]
    target_spec = spec_by_id[transfer.target_workload_id]
    if transfer.candidate_artifact_digest != source_run.champion_artifact_digest:
        raise ValueError("transfer evidence does not measure its source workload champion")
    reservation = next(
        (
            item
            for item in target_spec.transfer_protocols
            if item.source_workload_id == transfer.source_workload_id
            and item.target_workload_id == transfer.target_workload_id
            and item.dimensions == transfer.dimensions
        ),
        None,
    )
    if reservation is None or (
        transfer.primary.reservation != reservation.primary or transfer.confirmation.reservation != reservation.confirmation
    ):
        raise ValueError("transfer phases do not match their explicitly reserved route and protocol pair")
    if (
        reservation.primary.execution_environment_id != reservation.confirmation.execution_environment_id
        or reservation.primary.hardware_scope_id != reservation.confirmation.hardware_scope_id
    ):
        raise ValueError("transfer reservation phases disagree on their target hardware scope")
    for phase in (transfer.primary, transfer.confirmation):
        if phase.candidate_source_digest != source_run.champion_source_digest:
            raise ValueError("transfer evidence does not measure its source champion bytes")
        if (
            phase.incumbent_artifact_digest != target_spec.reference_artifact_digest
            or phase.incumbent_source_digest != target_spec.reference_source_digest
        ):
            raise ValueError("transfer evidence used the wrong target incumbent reference")
        validate_observation_policy(phase.observation, target_run.result)
        if phase.observation.report is None:
            continue
        if (
            phase.problem_id != target_spec.problem_id
            or phase.workload_family_id != target_spec.workload_family_id
            or phase.shape_profile_id != target_spec.shape_profile_id
            or phase.baseline_id != target_spec.reference_id
            or phase.protocol_compatibility_id != target_spec.protocol_compatibility_id
        ):
            raise ValueError("transfer evidence is bound to the wrong target benchmark identity")
        _validate_benchmark_requirements(phase.observation, target_spec, context="transfer")
    validate_transfer_phase_metadata(transfer, target_spec)
    if "workload-family" in transfer.dimensions and source_spec.workload_family_id == target_spec.workload_family_id:
        raise ValueError("workload-family transfer must cross workload-family identities")
    if "shape" in transfer.dimensions and source_spec.shape_profile_id == target_spec.shape_profile_id:
        raise ValueError("shape transfer must cross stable shape-profile identities")
    reserved_environment = reservation.primary.execution_environment_id
    reserved_scope = reservation.primary.hardware_scope_id
    if "hardware" in transfer.dimensions:
        if (
            reserved_environment == source_spec.execution_environment_id
            or reserved_scope == source_spec.primary_protocol.hardware_scope_id
        ):
            raise ValueError("hardware transfer must cross the source hardware scope and execution environment")
    elif (
        reserved_environment != target_spec.execution_environment_id
        or reserved_scope != target_spec.primary_protocol.hardware_scope_id
    ):
        raise ValueError("non-hardware transfer must use the pinned target hardware scope")

def validate_complete_study(
    report: KernelWorkloadStudyReport,
    *,
    validation_context: object = None,
) -> KernelWorkloadStudyReport:
    spec_ids = [spec.workload_id for spec in report.workload_specs]
    run_ids = [run.workload_id for run in report.workload_runs]
    if len(set(spec_ids)) != len(spec_ids):
        raise ValueError("study workload IDs must be unique")
    expected_specs_digest = canonical_digest(
        {
            "schema_version": "autocontext.kernel-workload-spec-set/v1",
            "workload_specs": [spec.model_dump(mode="json") for spec in report.workload_specs],
        }
    )
    if report.provenance.workload_specs_digest != expected_specs_digest:
        raise ValueError("study workload specifications disagree with their provenance digest")
    label_to_id: dict[str, str] = {}
    id_to_label: dict[str, str] = {}
    for spec in report.workload_specs:
        if (
            label_to_id.get(spec.workload_family, spec.workload_family_id) != spec.workload_family_id
            or id_to_label.get(spec.workload_family_id, spec.workload_family) != spec.workload_family
        ):
            raise ValueError("workload family labels and identities must have a one-to-one mapping")
        label_to_id[spec.workload_family] = spec.workload_family_id
        id_to_label[spec.workload_family_id] = spec.workload_family
    if len(id_to_label) < 3:
        raise ValueError("study must contain at least three cryptographically distinct workload families")
    if set(run_ids) != set(spec_ids) or len(run_ids) != len(spec_ids):
        raise ValueError("study must expose exactly one outcome for every workload")
    if len({run.run_id for run in report.workload_runs}) != len(report.workload_runs):
        raise ValueError("study workload runs must be distinct")
    reservations = [item for spec in report.workload_specs for item in spec.protocol_reservations]
    if len({item.protocol_id for item in reservations}) != len(reservations) or len(
        {item.plan_commitment for item in reservations}
    ) != len(reservations):
        raise ValueError("study protocol reservations must be globally fresh")
    spec_by_id = {spec.workload_id: spec for spec in report.workload_specs}
    run_by_id = {run.workload_id: run for run in report.workload_runs}
    if any(run.study_execution_id != report.provenance.study_execution_id for run in report.workload_runs):
        raise ValueError("workload run generation context belongs to a different study execution")
    for run in report.workload_runs:
        validate_run_against_spec(run, spec_by_id[run.workload_id])
    for transfer in report.transfers:
        _validate_transfer(transfer, spec_by_id=spec_by_id, run_by_id=run_by_id)
    _validate_provenance(report, report.provenance, validation_context=validation_context)
    expected_burns = protocol_burns(report.workload_specs, report.workload_runs, report.transfers)
    if report.protocol_burns != expected_burns:
        raise ValueError("protocol burn ledger disagrees with embedded evidence")
    expected_verified = all(run.independently_verified for run in report.workload_runs)
    if report.all_workloads_independently_verified != expected_verified:
        raise ValueError("study verification flag disagrees with per-workload evidence")
    expected_assessments = tuple(
        assess_champion(
            run,
            workload_ids=tuple(spec_ids),
            required_dimensions=set(spec_by_id[run.workload_id].required_transfer_dimensions),
            transfers=report.transfers,
        )
        for run in report.workload_runs
        if run.disposition == "promoted"
    )
    if report.champion_assessments != expected_assessments:
        raise ValueError("champion assessments disagree with transfer evidence")
    portable = tuple(item.candidate_artifact_digest for item in expected_assessments if item.disposition == "portable")
    if report.portable_champion_artifact_digests != portable:
        raise ValueError("portable champion list disagrees with transfer assessments")
    lessons = tuple(
        f"{item.source_workload_id} champion transferred to "
        f"{', '.join(workload for workload in item.passed_workload_ids if workload != item.source_workload_id)}"
        for item in expected_assessments
        if len(item.passed_workload_ids) > 1
    )
    regressions = tuple(
        f"{item.source_workload_id} champion failed required evidence on {', '.join(item.failed_workload_ids)}"
        for item in expected_assessments
        if item.failed_workload_ids
    )
    plateaus = tuple(
        f"{run.workload_id} exhausted its bounded proposals without a promotion"
        for run in report.workload_runs
        if run.disposition == "plateau"
    )
    incomplete = tuple(run.workload_id for run in report.workload_runs if run.disposition == "incomplete")
    if (report.transferable_lessons, report.regressions, report.plateaus, report.incomplete_workloads) != (
        lessons,
        regressions,
        plateaus,
        incomplete,
    ):
        raise ValueError("study narrative summaries disagree with replayed evidence")
    transfer_wall = sum(item.wall_seconds for item in report.transfers)
    transfer_cost = sum(item.cost_usd for item in report.transfers)
    total_wall = sum(float(run.total_wall_seconds) for run in report.workload_runs) + transfer_wall
    total_cost = sum(float(run.total_cost_usd) for run in report.workload_runs) + transfer_cost
    if (
        float(report.total_transfer_wall_seconds) != transfer_wall
        or float(report.total_transfer_cost_usd) != transfer_cost
        or float(report.total_wall_seconds) != total_wall
        or float(report.total_cost_usd) != total_cost
    ):
        raise ValueError("study wall or cost usage disagrees with embedded phase measurements")
    for run in report.workload_runs:
        outgoing_wall = sum(item.wall_seconds for item in report.transfers if item.source_workload_id == run.workload_id)
        outgoing_cost = sum(item.cost_usd for item in report.transfers if item.source_workload_id == run.workload_id)
        budget = spec_by_id[run.workload_id].budget
        if float(run.total_wall_seconds) + outgoing_wall > float(budget.max_workload_wall_seconds):
            raise ValueError(f"workload {run.workload_id!r} exceeded its wall budget including transfer evaluation")
        if float(run.total_cost_usd) + outgoing_cost > float(budget.max_workload_cost_usd):
            raise ValueError(f"workload {run.workload_id!r} exceeded its cost budget including transfer evaluation")
    return report


__all__ = [
    "assess_champion",
    "protocol_burns",
    "validate_complete_study",
    "validate_observation_report_identity",
    "validate_run_against_spec",
    "validate_spec_reservations",
    "workload_disposition",
]
