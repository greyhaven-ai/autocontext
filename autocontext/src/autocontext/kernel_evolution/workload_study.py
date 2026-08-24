"""Cross-workload evidence for bounded recursive kernel studies.

The kernel runner remains the authority for generation, evaluation, promotion,
confirmation, and lineage.  This module composes several completed runs without
making their incomparable scalar scores look like one aggregate benchmark.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from autocontext.kernel_evolution.generation import KernelGenerationBudgetState
from autocontext.kernel_evolution.models import (
    Digest,
    KernelBenchmarkObservation,
    KernelEvolutionResult,
    StrictModel,
    canonical_digest,
    kernel_benchmark_report_digest,
)

PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0)]
KernelStudyDimension = Literal["shape", "hardware", "workload-family"]
KernelWorkloadDisposition = Literal["promoted", "plateau"]
KernelTransferDisposition = Literal[
    "portable",
    "cross-workload",
    "specialist",
    "unconfirmed",
]


class KernelWorkloadBudget(StrictModel):
    """Pinned proposal, money, token, and wall-clock ceiling for one workload."""

    schema_version: Literal["autocontext.kernel-workload-budget/v1"] = "autocontext.kernel-workload-budget/v1"
    proposal_cap: int = Field(ge=1, le=10_000)
    max_cost_usd: PositiveFiniteFloat
    max_wall_seconds: PositiveFiniteFloat
    max_total_tokens: int = Field(ge=1)

    @property
    def budget_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class KernelWorkloadSpec(StrictModel):
    """Immutable public identity and evidence requirements for one workload."""

    schema_version: Literal["autocontext.kernel-workload-spec/v1"] = "autocontext.kernel-workload-spec/v1"
    workload_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    workload_family: str = Field(min_length=1, max_length=256)
    problem_id: str = Field(min_length=1, max_length=256)
    reference_id: Digest
    workload_family_id: Digest
    primary_protocol_id: Digest
    confirmation_protocol_ids: tuple[Digest, ...] = Field(min_length=1)
    protocol_compatibility_id: Digest
    required_correctness_slices: tuple[str, ...] = Field(min_length=2)
    required_transfer_dimensions: tuple[KernelStudyDimension, ...] = Field(min_length=1)
    minimum_case_speedup_vs_incumbent: PositiveFiniteFloat = 0.98
    budget: KernelWorkloadBudget
    reference_implementation: str = Field(min_length=1, max_length=512)
    task_prompt: str = Field(min_length=1, max_length=8_000)

    @model_validator(mode="after")
    def validate_unique_contract(self) -> Self:
        if len(set(self.confirmation_protocol_ids)) != len(self.confirmation_protocol_ids):
            raise ValueError("confirmation protocol identities must be unique")
        if self.primary_protocol_id in self.confirmation_protocol_ids:
            raise ValueError("primary and confirmation protocol identities must be fresh")
        for name, values in (
            ("required_correctness_slices", self.required_correctness_slices),
            ("required_transfer_dimensions", self.required_transfer_dimensions),
        ):
            if len(set(values)) != len(values) or any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain unique non-empty values")
        return self

    @property
    def spec_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class KernelWorkloadPhaseEvidence(StrictModel):
    """One independently measured primary or fresh-confirmation result."""

    schema_version: Literal["autocontext.kernel-workload-phase/v1"] = "autocontext.kernel-workload-phase/v1"
    role: Literal["primary", "confirmation"]
    report_digest: Digest
    candidate_artifact_digest: Digest
    candidate_source_digest: Digest
    hardware_scope_id: Digest
    execution_environment_id: Digest
    workload_family_id: Digest
    workload_fingerprint: Digest
    baseline_id: Digest
    protocol_id: Digest
    protocol_compatibility_id: Digest
    plan_commitment: Digest
    correctness_slices: tuple[str, ...] = Field(min_length=1)
    all_correctness_slices_passed: bool
    all_case_floors_passed: bool
    minimum_case_speedup_vs_incumbent: PositiveFiniteFloat
    speedup_vs_incumbent: PositiveFiniteFloat
    speedup_vs_reference: PositiveFiniteFloat

    @property
    def passed(self) -> bool:
        return self.all_correctness_slices_passed and self.all_case_floors_passed


class KernelWorkloadRunEvidence(StrictModel):
    """Per-workload outcome; never hidden behind a study-wide scalar score."""

    schema_version: Literal["autocontext.kernel-workload-run/v1"] = "autocontext.kernel-workload-run/v1"
    workload_spec_id: Digest
    workload_id: str
    workload_family: str
    run_id: str
    result_digest: Digest
    champion_artifact_digest: Digest
    champion_source_digest: Digest
    disposition: KernelWorkloadDisposition
    primary: KernelWorkloadPhaseEvidence
    confirmation: KernelWorkloadPhaseEvidence
    budget_id: Digest
    budget_state: KernelGenerationBudgetState
    proposals_evaluated: int = Field(ge=0)
    strategy_tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_independent_phases(self) -> Self:
        if self.primary.role != "primary" or self.confirmation.role != "confirmation":
            raise ValueError("workload evidence requires primary and confirmation roles")
        if self.primary.protocol_id == self.confirmation.protocol_id:
            raise ValueError("workload confirmation must use a fresh protocol identity")
        if self.primary.plan_commitment == self.confirmation.plan_commitment:
            raise ValueError("workload confirmation must use fresh plan material")
        if self.primary.candidate_artifact_digest != self.champion_artifact_digest:
            raise ValueError("primary evidence does not measure the declared champion")
        if self.confirmation.candidate_artifact_digest != self.champion_artifact_digest:
            raise ValueError("confirmation evidence does not measure the declared champion")
        if self.primary.candidate_source_digest != self.champion_source_digest:
            raise ValueError("primary evidence does not measure the declared champion source")
        if self.confirmation.candidate_source_digest != self.champion_source_digest:
            raise ValueError("confirmation evidence does not measure the declared champion source")
        if len(set(self.strategy_tags)) != len(self.strategy_tags):
            raise ValueError("strategy tags must be unique")
        return self

    @property
    def independently_verified(self) -> bool:
        return self.primary.passed and self.confirmation.passed


class KernelTransferPhaseEvidence(StrictModel):
    """One target-side transfer result, including fail-closed rejections."""

    role: Literal["primary", "confirmation"]
    candidate_artifact_digest: Digest
    candidate_source_digest: Digest
    eligible: bool
    rejection_reason: str | None = None
    report_digest: Digest | None = None
    hardware_scope_id: Digest | None = None
    execution_environment_id: Digest | None = None
    workload_family_id: Digest | None = None
    workload_fingerprint: Digest | None = None
    baseline_id: Digest | None = None
    protocol_id: Digest | None = None
    protocol_compatibility_id: Digest | None = None
    plan_commitment: Digest | None = None
    correctness_slices: tuple[str, ...] = ()
    all_correctness_slices_passed: bool
    all_case_floors_passed: bool
    minimum_case_speedup_vs_incumbent: PositiveFiniteFloat | None = None

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if self.eligible and self.rejection_reason is not None:
            raise ValueError("eligible transfer phases cannot have a rejection reason")
        if not self.eligible and not self.rejection_reason:
            raise ValueError("ineligible transfer phases require a rejection reason")
        if self.eligible and any(
            value is None
            for value in (
                self.report_digest,
                self.hardware_scope_id,
                self.execution_environment_id,
                self.workload_family_id,
                self.workload_fingerprint,
                self.baseline_id,
                self.protocol_id,
                self.protocol_compatibility_id,
                self.plan_commitment,
                self.minimum_case_speedup_vs_incumbent,
            )
        ):
            raise ValueError("eligible transfer phases require complete target evidence")
        report_identity = (
            self.hardware_scope_id,
            self.execution_environment_id,
            self.workload_family_id,
            self.workload_fingerprint,
            self.baseline_id,
            self.protocol_id,
            self.protocol_compatibility_id,
            self.plan_commitment,
        )
        if self.report_digest is not None and any(value is None for value in report_identity):
            raise ValueError("transfer reports require complete target identity")
        if self.report_digest is None and (
            any(value is not None for value in report_identity)
            or self.correctness_slices
            or self.all_correctness_slices_passed
            or self.all_case_floors_passed
            or self.minimum_case_speedup_vs_incumbent is not None
        ):
            raise ValueError("reportless transfer rejections cannot claim benchmark evidence")
        return self

    @property
    def passed(self) -> bool:
        return self.eligible and self.all_correctness_slices_passed and self.all_case_floors_passed


class KernelTransferEvidence(StrictModel):
    """Independent re-evaluation of one champion outside its source workload."""

    schema_version: Literal["autocontext.kernel-transfer/v1"] = "autocontext.kernel-transfer/v1"
    source_workload_id: str
    target_workload_id: str
    candidate_artifact_digest: Digest
    dimensions: tuple[KernelStudyDimension, ...] = Field(min_length=1)
    primary: KernelTransferPhaseEvidence
    confirmation: KernelTransferPhaseEvidence

    @model_validator(mode="after")
    def validate_transfer(self) -> Self:
        if self.source_workload_id == self.target_workload_id and "hardware" not in self.dimensions:
            raise ValueError("same-workload transfer evidence must cross a hardware environment")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("transfer dimensions must be unique")
        for phase in (self.primary, self.confirmation):
            if phase.candidate_artifact_digest != self.candidate_artifact_digest:
                raise ValueError("transfer phase measures a different candidate artifact")
        if self.primary.candidate_source_digest != self.confirmation.candidate_source_digest:
            raise ValueError("transfer phases measure different candidate sources")
        if self.primary.role != "primary" or self.confirmation.role != "confirmation":
            raise ValueError("transfer evidence requires independent primary and confirmation phases")
        if self.primary.protocol_id is not None and self.primary.protocol_id == self.confirmation.protocol_id:
            raise ValueError("transfer confirmation must use a fresh protocol identity")
        return self

    @property
    def passed(self) -> bool:
        return self.primary.passed and self.confirmation.passed


class KernelChampionTransferAssessment(StrictModel):
    """Portable, partial, specialist, or incompletely tested champion."""

    candidate_artifact_digest: Digest
    source_workload_id: str
    passed_workload_ids: tuple[str, ...]
    failed_workload_ids: tuple[str, ...]
    untested_workload_ids: tuple[str, ...]
    covered_dimensions: tuple[KernelStudyDimension, ...]
    disposition: KernelTransferDisposition


class KernelWorkloadStudyReport(StrictModel):
    """Replayable multi-workload report with no invalid aggregate promotion."""

    schema_version: Literal["autocontext.kernel-workload-study/v1"] = "autocontext.kernel-workload-study/v1"
    study_name: str = Field(min_length=1, max_length=256)
    created_at: str
    workload_specs: tuple[KernelWorkloadSpec, ...] = Field(min_length=3)
    workload_runs: tuple[KernelWorkloadRunEvidence, ...] = Field(min_length=3)
    transfers: tuple[KernelTransferEvidence, ...] = ()
    champion_assessments: tuple[KernelChampionTransferAssessment, ...] = ()
    portable_champion_artifact_digests: tuple[Digest, ...] = ()
    transferable_lessons: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    plateaus: tuple[str, ...] = ()
    all_workloads_independently_verified: bool

    @model_validator(mode="after")
    def validate_complete_study(self) -> Self:
        spec_ids = [spec.workload_id for spec in self.workload_specs]
        run_ids = [run.workload_id for run in self.workload_runs]
        if len(set(spec_ids)) != len(spec_ids):
            raise ValueError("study workload IDs must be unique")
        if len({spec.workload_family for spec in self.workload_specs}) < 3:
            raise ValueError("study must contain at least three workload families")
        if set(run_ids) != set(spec_ids) or len(run_ids) != len(spec_ids):
            raise ValueError("study must expose exactly one outcome for every workload")
        spec_by_id = {spec.workload_id: spec for spec in self.workload_specs}
        for run in self.workload_runs:
            spec = spec_by_id[run.workload_id]
            _validate_run_against_spec(run, spec)
        run_by_id = {run.workload_id: run for run in self.workload_runs}
        for transfer in self.transfers:
            if transfer.source_workload_id not in spec_by_id or transfer.target_workload_id not in spec_by_id:
                raise ValueError("transfer evidence references a workload outside the study")
            source_run = run_by_id[transfer.source_workload_id]
            target_spec = spec_by_id[transfer.target_workload_id]
            if transfer.candidate_artifact_digest != source_run.champion_artifact_digest:
                raise ValueError("transfer evidence does not measure its source workload champion")
            for phase in (transfer.primary, transfer.confirmation):
                if phase.candidate_source_digest != source_run.champion_source_digest:
                    raise ValueError("transfer evidence does not measure its source champion bytes")
                if phase.report_digest is None:
                    continue
                if phase.workload_family_id != target_spec.workload_family_id:
                    raise ValueError("transfer evidence is bound to the wrong target workload family")
                if phase.baseline_id != target_spec.reference_id:
                    raise ValueError("transfer evidence is bound to the wrong target reference")
                if phase.protocol_compatibility_id != target_spec.protocol_compatibility_id:
                    raise ValueError("transfer evidence is bound to an incompatible target protocol")
                if phase.minimum_case_speedup_vs_incumbent is not None and float(
                    phase.minimum_case_speedup_vs_incumbent
                ) != float(target_spec.minimum_case_speedup_vs_incumbent):
                    raise ValueError("transfer evidence is bound to the wrong target case floor")
                if phase.eligible and not set(target_spec.required_correctness_slices) <= set(phase.correctness_slices):
                    raise ValueError("eligible transfer evidence omitted target correctness slices")
            if transfer.primary.protocol_id is not None and transfer.primary.protocol_id != target_spec.primary_protocol_id:
                raise ValueError("transfer evidence has the wrong target primary protocol")
            if (
                transfer.confirmation.protocol_id is not None
                and transfer.confirmation.protocol_id not in target_spec.confirmation_protocol_ids
            ):
                raise ValueError("transfer evidence has an unreserved target confirmation protocol")
            if "workload-family" in transfer.dimensions and (
                spec_by_id[transfer.source_workload_id].workload_family == target_spec.workload_family
            ):
                raise ValueError("workload-family transfer must cross workload families")
            for phase, source_phase in (
                (transfer.primary, source_run.primary),
                (transfer.confirmation, source_run.confirmation),
            ):
                if "hardware" in transfer.dimensions and phase.execution_environment_id == source_phase.execution_environment_id:
                    raise ValueError("hardware transfer must cross execution environments")
                if "shape" in transfer.dimensions and phase.workload_fingerprint == source_phase.workload_fingerprint:
                    raise ValueError("shape transfer must cross workload fingerprints")
        expected_verified = all(run.independently_verified for run in self.workload_runs)
        if self.all_workloads_independently_verified != expected_verified:
            raise ValueError("study verification flag disagrees with per-workload evidence")
        expected_assessments = tuple(
            _assess_champion(
                run,
                workload_ids=tuple(spec_ids),
                required_dimensions=set(spec_by_id[run.workload_id].required_transfer_dimensions),
                transfers=self.transfers,
            )
            for run in self.workload_runs
            if run.disposition == "promoted"
        )
        if self.champion_assessments != expected_assessments:
            raise ValueError("champion assessments disagree with transfer evidence")
        portable = tuple(
            assessment.candidate_artifact_digest
            for assessment in self.champion_assessments
            if assessment.disposition == "portable"
        )
        if self.portable_champion_artifact_digests != portable:
            raise ValueError("portable champion list disagrees with transfer assessments")
        expected_lessons = tuple(
            f"{assessment.source_workload_id} champion transferred to "
            f"{', '.join(workload for workload in assessment.passed_workload_ids if workload != assessment.source_workload_id)}"
            for assessment in self.champion_assessments
            if len(assessment.passed_workload_ids) > 1
        )
        expected_regressions = tuple(
            f"{assessment.source_workload_id} champion failed required evidence on {', '.join(assessment.failed_workload_ids)}"
            for assessment in self.champion_assessments
            if assessment.failed_workload_ids
        )
        expected_plateaus = tuple(
            f"{run.workload_id} exhausted its bounded proposals without a promotion"
            for run in self.workload_runs
            if run.disposition == "plateau"
        )
        if self.transferable_lessons != expected_lessons:
            raise ValueError("transferable lessons disagree with champion assessments")
        if self.regressions != expected_regressions:
            raise ValueError("regressions disagree with champion assessments")
        if self.plateaus != expected_plateaus:
            raise ValueError("plateaus disagree with per-workload outcomes")
        return self

    @property
    def study_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json", exclude={"created_at"}))


def _phase_from_observation(
    observation: KernelBenchmarkObservation,
    *,
    role: Literal["primary", "confirmation"],
) -> KernelWorkloadPhaseEvidence:
    if not observation.eligible or observation.report is None:
        reason = observation.rejection_reason or "ineligible"
        raise ValueError(f"{role} workload evidence is not eligible: {reason}")
    report = observation.report
    if report.correctness is None or report.performance is None:
        raise ValueError(f"{role} workload evidence omitted correctness or performance")
    slices = tuple(item.name for item in report.correctness.slices)
    if not slices:
        raise ValueError(f"{role} workload evidence requires named correctness slices")
    floors_passed = bool(report.performance.cases) and all(item.passed_no_regression for item in report.performance.cases)
    case_floors = {float(item.minimum_speedup_vs_incumbent) for item in report.performance.cases}
    if len(case_floors) != 1:
        raise ValueError(f"{role} workload evidence requires one canonical case floor")
    if observation.speedup_vs_incumbent is None or observation.speedup_vs_reference is None:
        raise ValueError(f"{role} workload evidence omitted derived speedups")
    return KernelWorkloadPhaseEvidence(
        role=role,
        report_digest=kernel_benchmark_report_digest(report),
        candidate_artifact_digest=report.candidate_artifact_digest,
        candidate_source_digest=report.candidate_source_digest,
        hardware_scope_id=report.hardware_scope_id,
        execution_environment_id=report.hardware.execution_environment_id,
        workload_family_id=report.hardware.workload_family_id,
        workload_fingerprint=report.hardware.workload_fingerprint,
        baseline_id=report.baseline_id,
        protocol_id=report.protocol.protocol_id,
        protocol_compatibility_id=report.protocol.compatibility_id,
        plan_commitment=report.protocol.seed_commitment,
        correctness_slices=slices,
        all_correctness_slices_passed=report.correctness.passed and all(item.passed for item in report.correctness.slices),
        all_case_floors_passed=floors_passed,
        minimum_case_speedup_vs_incumbent=case_floors.pop(),
        speedup_vs_incumbent=observation.speedup_vs_incumbent,
        speedup_vs_reference=observation.speedup_vs_reference,
    )


def _transfer_phase_from_observation(
    observation: KernelBenchmarkObservation,
    *,
    role: Literal["primary", "confirmation"],
) -> KernelTransferPhaseEvidence:
    report = observation.report
    correctness_slices = (
        tuple(item.name for item in report.correctness.slices) if report is not None and report.correctness is not None else ()
    )
    correctness_passed = bool(report is not None and report.correctness is not None and report.correctness.passed)
    floors_passed = bool(
        report is not None
        and report.performance is not None
        and report.performance.cases
        and all(item.passed_no_regression for item in report.performance.cases)
    )
    case_floors = (
        {float(item.minimum_speedup_vs_incumbent) for item in report.performance.cases}
        if report is not None and report.performance is not None
        else set()
    )
    if len(case_floors) > 1:
        raise ValueError(f"{role} transfer evidence contains inconsistent case floors")
    return KernelTransferPhaseEvidence(
        role=role,
        candidate_artifact_digest=observation.candidate_artifact_digest,
        candidate_source_digest=observation.candidate_source_digest,
        eligible=observation.eligible,
        rejection_reason=observation.rejection_reason,
        report_digest=kernel_benchmark_report_digest(report) if report is not None else None,
        hardware_scope_id=report.hardware_scope_id if report is not None else None,
        execution_environment_id=(report.hardware.execution_environment_id if report is not None else None),
        workload_family_id=report.hardware.workload_family_id if report is not None else None,
        workload_fingerprint=report.hardware.workload_fingerprint if report is not None else None,
        baseline_id=report.baseline_id if report is not None else observation.baseline_id,
        protocol_id=report.protocol.protocol_id if report is not None else observation.protocol_id,
        protocol_compatibility_id=(
            report.protocol.compatibility_id if report is not None else observation.protocol_compatibility_id
        ),
        plan_commitment=report.protocol.seed_commitment if report is not None else None,
        correctness_slices=correctness_slices,
        all_correctness_slices_passed=correctness_passed,
        all_case_floors_passed=floors_passed,
        minimum_case_speedup_vs_incumbent=next(iter(case_floors), None),
    )


def _validate_run_against_spec(run: KernelWorkloadRunEvidence, spec: KernelWorkloadSpec) -> None:
    if run.workload_spec_id != spec.spec_id or run.workload_family != spec.workload_family:
        raise ValueError(f"workload run {run.workload_id!r} is bound to a different specification")
    if run.budget_id != spec.budget.budget_id:
        raise ValueError(f"workload run {run.workload_id!r} is bound to a different budget")
    if run.proposals_evaluated > spec.budget.proposal_cap:
        raise ValueError(f"workload run {run.workload_id!r} exceeded its proposal budget")
    state = run.budget_state
    if state.completed_proposals != run.proposals_evaluated:
        raise ValueError(f"workload run {run.workload_id!r} proposal count disagrees with its budget state")
    if float(state.cost_usd) > float(spec.budget.max_cost_usd):
        raise ValueError(f"workload run {run.workload_id!r} exceeded its cost budget")
    if float(state.wall_seconds) > float(spec.budget.max_wall_seconds):
        raise ValueError(f"workload run {run.workload_id!r} exceeded its wall-clock budget")
    if state.total_tokens > spec.budget.max_total_tokens:
        raise ValueError(f"workload run {run.workload_id!r} exceeded its token budget")
    required_slices = set(spec.required_correctness_slices)
    for phase in (run.primary, run.confirmation):
        if phase.workload_family_id != spec.workload_family_id or phase.baseline_id != spec.reference_id:
            raise ValueError(f"workload run {run.workload_id!r} has the wrong family or reference identity")
        if phase.protocol_compatibility_id != spec.protocol_compatibility_id:
            raise ValueError(f"workload run {run.workload_id!r} has an incompatible protocol")
        if float(phase.minimum_case_speedup_vs_incumbent) != float(spec.minimum_case_speedup_vs_incumbent):
            raise ValueError(f"workload run {run.workload_id!r} has the wrong case floor")
        if not required_slices <= set(phase.correctness_slices):
            raise ValueError(f"workload run {run.workload_id!r} omitted required correctness slices")
    if run.primary.protocol_id != spec.primary_protocol_id:
        raise ValueError(f"workload run {run.workload_id!r} has the wrong primary protocol")
    if run.confirmation.protocol_id not in spec.confirmation_protocol_ids:
        raise ValueError(f"workload run {run.workload_id!r} has an unreserved confirmation protocol")


def build_kernel_workload_run_evidence(
    *,
    spec: KernelWorkloadSpec,
    result: KernelEvolutionResult,
    primary_observation: KernelBenchmarkObservation,
    confirmation_observation: KernelBenchmarkObservation,
    budget_state: KernelGenerationBudgetState,
    strategy_tags: tuple[str, ...] = (),
) -> KernelWorkloadRunEvidence:
    """Bind a completed runner result to independent final-champion evidence."""
    if result.problem_id != spec.problem_id:
        raise ValueError("kernel result problem does not match the workload specification")
    promoted = any(
        attempt.artifact_digest == result.champion_artifact_digest and attempt.decision == "promoted"
        for attempt in result.attempts
    )
    primary = _phase_from_observation(primary_observation, role="primary")
    confirmation = _phase_from_observation(confirmation_observation, role="confirmation")
    run = KernelWorkloadRunEvidence(
        workload_spec_id=spec.spec_id,
        workload_id=spec.workload_id,
        workload_family=spec.workload_family,
        run_id=result.run_id,
        result_digest=canonical_digest(result.model_dump(mode="json")),
        champion_artifact_digest=result.champion_artifact_digest,
        champion_source_digest=result.champion_source_digest,
        disposition="promoted" if promoted else "plateau",
        primary=primary,
        confirmation=confirmation,
        budget_id=spec.budget.budget_id,
        budget_state=budget_state,
        proposals_evaluated=sum(attempt.role == "candidate" for attempt in result.attempts),
        strategy_tags=strategy_tags,
    )
    _validate_run_against_spec(run, spec)
    return run


def build_kernel_transfer_evidence(
    *,
    source_workload_id: str,
    target_workload_id: str,
    dimensions: tuple[KernelStudyDimension, ...],
    primary_observation: KernelBenchmarkObservation,
    confirmation_observation: KernelBenchmarkObservation,
) -> KernelTransferEvidence:
    """Build an independent cross-workload or cross-hardware transfer receipt."""
    primary = _transfer_phase_from_observation(primary_observation, role="primary")
    confirmation = _transfer_phase_from_observation(confirmation_observation, role="confirmation")
    return KernelTransferEvidence(
        source_workload_id=source_workload_id,
        target_workload_id=target_workload_id,
        candidate_artifact_digest=primary.candidate_artifact_digest,
        dimensions=dimensions,
        primary=primary,
        confirmation=confirmation,
    )


def _assess_champion(
    run: KernelWorkloadRunEvidence,
    *,
    workload_ids: tuple[str, ...],
    required_dimensions: set[KernelStudyDimension],
    transfers: tuple[KernelTransferEvidence, ...],
) -> KernelChampionTransferAssessment:
    relevant = tuple(
        item
        for item in transfers
        if item.source_workload_id == run.workload_id and item.candidate_artifact_digest == run.champion_artifact_digest
    )
    passed = {run.workload_id} if run.independently_verified else set()
    failed: set[str] = set()
    dimensions: set[KernelStudyDimension] = set()
    for transfer in relevant:
        dimensions.update(transfer.dimensions)
        (passed if transfer.passed else failed).add(transfer.target_workload_id)
    untested = set(workload_ids) - passed - failed
    if not untested and not failed and required_dimensions <= dimensions:
        disposition: KernelTransferDisposition = "portable"
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


def build_kernel_workload_study_report(
    *,
    study_name: str,
    specs: tuple[KernelWorkloadSpec, ...],
    runs: tuple[KernelWorkloadRunEvidence, ...],
    transfers: tuple[KernelTransferEvidence, ...] = (),
    created_at: str | None = None,
) -> KernelWorkloadStudyReport:
    """Create a study report whose only aggregate promotions are fully portable."""
    workload_ids = tuple(spec.workload_id for spec in specs)
    spec_by_id = {spec.workload_id: spec for spec in specs}
    assessments = tuple(
        _assess_champion(
            run,
            workload_ids=workload_ids,
            required_dimensions=set(spec_by_id[run.workload_id].required_transfer_dimensions),
            transfers=transfers,
        )
        for run in runs
        if run.disposition == "promoted"
    )
    portable = tuple(assessment.candidate_artifact_digest for assessment in assessments if assessment.disposition == "portable")
    transferable_lessons = tuple(
        f"{assessment.source_workload_id} champion transferred to "
        f"{', '.join(workload for workload in assessment.passed_workload_ids if workload != assessment.source_workload_id)}"
        for assessment in assessments
        if len(assessment.passed_workload_ids) > 1
    )
    regressions = tuple(
        f"{assessment.source_workload_id} champion failed required evidence on {', '.join(assessment.failed_workload_ids)}"
        for assessment in assessments
        if assessment.failed_workload_ids
    )
    plateaus = tuple(
        f"{run.workload_id} exhausted its bounded proposals without a promotion" for run in runs if run.disposition == "plateau"
    )
    return KernelWorkloadStudyReport(
        study_name=study_name,
        created_at=created_at or datetime.now(UTC).isoformat(),
        workload_specs=specs,
        workload_runs=runs,
        transfers=transfers,
        champion_assessments=assessments,
        portable_champion_artifact_digests=portable,
        transferable_lessons=transferable_lessons,
        regressions=regressions,
        plateaus=plateaus,
        all_workloads_independently_verified=all(run.independently_verified for run in runs),
    )


__all__ = [
    "KernelChampionTransferAssessment",
    "KernelStudyDimension",
    "KernelTransferEvidence",
    "KernelTransferPhaseEvidence",
    "KernelWorkloadBudget",
    "KernelWorkloadDisposition",
    "KernelWorkloadPhaseEvidence",
    "KernelWorkloadRunEvidence",
    "KernelWorkloadSpec",
    "KernelWorkloadStudyReport",
    "build_kernel_transfer_evidence",
    "build_kernel_workload_run_evidence",
    "build_kernel_workload_study_report",
]
