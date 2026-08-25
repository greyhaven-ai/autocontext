"""Replayable cross-workload evidence for bounded recursive kernel studies."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, ValidationInfo, model_validator

from autocontext.kernel_evolution._workload_study_builder import workload_study_report_payload
from autocontext.kernel_evolution.evidence_replay import validate_eligible_observation
from autocontext.kernel_evolution.generation import (
    KernelGenerationBudget,
    KernelGenerationBudgetState,
    KernelGenerationFailure,
    KernelGenerationResult,
)
from autocontext.kernel_evolution.models import (
    Digest,
    KernelBenchmarkObservation,
    KernelEvolutionResult,
    StrictModel,
    artifact_digest_from_source_digest,
    canonical_digest,
    kernel_benchmark_report_digest,
)
from autocontext.kernel_evolution.protocols import KernelDecisionPolicy
from autocontext.kernel_evolution.workload_study_rules import (
    kernel_generation_receipt_context_digest,
    validate_reportless_observation,
)
from autocontext.kernel_evolution.workload_study_validation import (
    validate_observation_report_identity,
    validate_run_against_spec,
    validate_spec_reservations,
    workload_disposition,
)

PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0)]
KernelStudyDimension = Literal["shape", "hardware", "workload-family"]
KernelWorkloadDisposition = Literal["promoted", "plateau", "incomplete"]
KernelTransferDisposition = Literal["portable", "cross-workload", "specialist", "unconfirmed"]
KernelStudyEvidenceKind = Literal["synthetic", "measured"]
KernelBenchmarkCaseIdentity = Annotated[str, Field(pattern=r"^(?:train|holdout):.+$")]
KernelProtocolBurnKind = Literal[
    "campaign-primary",
    "campaign-confirmation",
    "final-primary",
    "final-confirmation",
    "transfer-primary",
    "transfer-confirmation",
]
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class KernelProtocolReservation(StrictModel):
    """One host-reserved protocol and private-plan identity."""

    protocol_id: Digest
    plan_commitment: Digest
    hardware_scope_id: Digest
    execution_environment_id: Digest


class KernelTransferProtocolReservation(StrictModel):
    """A fresh primary/confirmation pair reserved for one transfer route."""

    source_workload_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    target_workload_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    dimensions: tuple[KernelStudyDimension, ...] = Field(min_length=1)
    primary: KernelProtocolReservation
    confirmation: KernelProtocolReservation

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("transfer reservation dimensions must be unique")
        if self.dimensions != tuple(sorted(self.dimensions)):
            raise ValueError("transfer reservation dimensions must use canonical sorted order")
        if self.source_workload_id == self.target_workload_id and self.dimensions != ("hardware",):
            raise ValueError("same-workload transfer reservations must be hardware-only")
        if self.primary.protocol_id == self.confirmation.protocol_id:
            raise ValueError("transfer confirmation requires a fresh protocol")
        if self.primary.plan_commitment == self.confirmation.plan_commitment:
            raise ValueError("transfer confirmation requires a fresh private plan")
        if (
            self.primary.execution_environment_id != self.confirmation.execution_environment_id
            or self.primary.hardware_scope_id != self.confirmation.hardware_scope_id
        ):
            raise ValueError("transfer confirmation must use the same target hardware scope")
        return self


class KernelProtocolBurn(StrictModel):
    """One consumed protocol/plan pair in replay order."""

    workload_id: str
    evidence_id: str
    kind: KernelProtocolBurnKind
    protocol_id: Digest
    plan_commitment: Digest


class KernelWorkloadStudyProvenance(StrictModel):
    """Provenance that prevents synthetic evidence being mistaken for measurements."""

    evidence_kind: KernelStudyEvidenceKind
    study_execution_id: Digest
    workload_specs_digest: Digest
    manifest_digest: Digest
    contract_digest: Digest
    evidence_index_digest: Digest
    backend_identity: str = Field(min_length=1, max_length=512)
    warning: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_warning(self) -> Self:
        if not self.backend_identity.strip():
            raise ValueError("study backend identity must not be empty")
        if self.evidence_kind == "synthetic" and (self.warning is None or not self.warning.strip()):
            raise ValueError("synthetic study evidence requires a conspicuous warning")
        return self


class KernelWorkloadBudget(StrictModel):
    """Exact generation budget plus an end-to-end workload wall ceiling."""

    schema_version: Literal["autocontext.kernel-workload-budget/v1"] = "autocontext.kernel-workload-budget/v1"
    generation_budget: KernelGenerationBudget
    max_workload_wall_seconds: PositiveFiniteFloat
    max_workload_cost_usd: PositiveFiniteFloat

    @property
    def proposal_cap(self) -> int:
        return self.generation_budget.proposal_cap

    @property
    def max_cost_usd(self) -> float:
        return float(self.generation_budget.max_cost_usd)

    @property
    def max_total_tokens(self) -> int:
        return self.generation_budget.max_total_tokens

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
    reference_artifact_digest: Digest
    reference_source_digest: Digest
    reference_source_suffix: str = ".py"
    reference_entrypoint: str = "ModelNew"
    workload_family_id: Digest
    shape_profile_id: Digest
    execution_environment_id: Digest
    decision_policy: KernelDecisionPolicy
    primary_protocol: KernelProtocolReservation
    confirmation_protocols: tuple[KernelProtocolReservation, ...] = Field(min_length=1)
    final_primary_protocol: KernelProtocolReservation
    final_confirmation_protocol: KernelProtocolReservation
    transfer_protocols: tuple[KernelTransferProtocolReservation, ...] = ()
    protocol_compatibility_id: Digest
    required_correctness_slices: tuple[str, ...] = Field(min_length=2)
    required_benchmark_cases: tuple[KernelBenchmarkCaseIdentity, ...] = Field(min_length=1)
    required_transfer_dimensions: tuple[KernelStudyDimension, ...] = Field(min_length=1)
    minimum_case_speedup_vs_incumbent: PositiveFiniteFloat = 0.98
    budget: KernelWorkloadBudget
    reference_implementation: str = Field(min_length=1, max_length=512)
    task_prompt: str = Field(min_length=1, max_length=8_000)

    @model_validator(mode="after")
    def validate_unique_contract(self) -> Self:
        if not self.workload_family.strip() or not self.problem_id.strip():
            raise ValueError("workload family and problem identity must not be empty")
        expected_reference = artifact_digest_from_source_digest(
            self.reference_source_digest,
            source_suffix=self.reference_source_suffix,
            entrypoint=self.reference_entrypoint,
        )
        if self.reference_artifact_digest != expected_reference:
            raise ValueError("reference artifact does not match its source digest and ABI")
        if any(item.target_workload_id != self.workload_id for item in self.transfer_protocols):
            raise ValueError("transfer reservation target must match its workload specification")
        routes = [(item.source_workload_id, item.target_workload_id, item.dimensions) for item in self.transfer_protocols]
        if len(set(routes)) != len(routes):
            raise ValueError("transfer routes and dimension sets must be unique")
        reservations = self.protocol_reservations
        if len({item.protocol_id for item in reservations}) != len(reservations):
            raise ValueError("workload protocol identities must be globally fresh")
        if len({item.plan_commitment for item in reservations}) != len(reservations):
            raise ValueError("workload private-plan commitments must be globally fresh")
        for name, values in (
            ("required_correctness_slices", self.required_correctness_slices),
            ("required_benchmark_cases", self.required_benchmark_cases),
            ("required_transfer_dimensions", self.required_transfer_dimensions),
        ):
            if len(set(values)) != len(values) or any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain unique non-empty values")
        validate_spec_reservations(self)
        return self

    @property
    def protocol_reservations(self) -> tuple[KernelProtocolReservation, ...]:
        return (
            self.primary_protocol,
            *self.confirmation_protocols,
            self.final_primary_protocol,
            self.final_confirmation_protocol,
            *(item.primary for item in self.transfer_protocols),
            *(item.confirmation for item in self.transfer_protocols),
        )

    @property
    def primary_protocol_id(self) -> str:
        return self.primary_protocol.protocol_id

    @property
    def confirmation_protocol_ids(self) -> tuple[str, ...]:
        return tuple(item.protocol_id for item in self.confirmation_protocols)

    @property
    def spec_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


def _shape_profile_id(observation: KernelBenchmarkObservation) -> str | None:
    report = observation.report
    value = report.metadata.get("shape_profile_id") if report is not None else None
    if value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("benchmark report shape_profile_id must be a branded SHA-256 digest")
    return value


class _KernelObservedPhase(StrictModel):
    role: Literal["primary", "confirmation"]
    observation: KernelBenchmarkObservation
    reservation: KernelProtocolReservation
    evaluation_wall_seconds: PositiveFiniteFloat
    evaluation_cost_usd: NonNegativeFiniteFloat

    @model_validator(mode="after")
    def validate_observation_reservation(self) -> Self:
        report = self.observation.report
        validate_observation_report_identity(self.observation)
        validate_reportless_observation(self.observation)
        if report is not None and (
            report.protocol.protocol_id != self.reservation.protocol_id
            or report.protocol.seed_commitment != self.reservation.plan_commitment
            or report.hardware_scope_id != self.reservation.hardware_scope_id
            or report.hardware.execution_environment_id != self.reservation.execution_environment_id
        ):
            raise ValueError("phase observation disagrees with its reserved protocol, plan, or hardware")
        if self.observation.eligible:
            if report is None or report.correctness is None or report.performance is None:
                raise ValueError("eligible phase evidence requires complete correctness and performance")
            if not report.correctness.slices or not report.performance.cases:
                raise ValueError("eligible phase evidence requires named slices and per-case floors")
            if len({float(item.minimum_speedup_vs_incumbent) for item in report.performance.cases}) != 1:
                raise ValueError("phase evidence requires one canonical case floor")
            validate_eligible_observation(self.observation)
        _shape_profile_id(self.observation)
        return self

    @property
    def report_digest(self) -> str | None:
        report = self.observation.report
        return kernel_benchmark_report_digest(report) if report is not None else None

    @property
    def candidate_artifact_digest(self) -> str:
        return self.observation.candidate_artifact_digest

    @property
    def candidate_source_digest(self) -> str:
        return self.observation.candidate_source_digest

    @property
    def incumbent_artifact_digest(self) -> str:
        return self.observation.incumbent_artifact_digest

    @property
    def incumbent_source_digest(self) -> str:
        return self.observation.incumbent_source_digest

    @property
    def eligible(self) -> bool:
        return self.observation.eligible

    @property
    def rejection_reason(self) -> str | None:
        return self.observation.rejection_reason

    @property
    def protocol_id(self) -> str:
        return self.reservation.protocol_id

    @property
    def protocol_compatibility_id(self) -> str | None:
        return self.observation.protocol_compatibility_id

    @property
    def plan_commitment(self) -> str:
        return self.reservation.plan_commitment

    @property
    def problem_id(self) -> str | None:
        report = self.observation.report
        return report.problem_id if report is not None else None

    @property
    def hardware_scope_id(self) -> str | None:
        return self.observation.hardware_scope_id

    @property
    def execution_environment_id(self) -> str | None:
        report = self.observation.report
        return report.hardware.execution_environment_id if report is not None else None

    @property
    def workload_family_id(self) -> str | None:
        report = self.observation.report
        return report.hardware.workload_family_id if report is not None else None

    @property
    def workload_fingerprint(self) -> str | None:
        report = self.observation.report
        return report.hardware.workload_fingerprint if report is not None else None

    @property
    def shape_profile_id(self) -> str | None:
        return _shape_profile_id(self.observation)

    @property
    def baseline_id(self) -> str | None:
        return self.observation.baseline_id

    @property
    def correctness_slices(self) -> tuple[str, ...]:
        report = self.observation.report
        if report is None or report.correctness is None:
            return ()
        return tuple(item.name for item in report.correctness.slices)

    @property
    def all_correctness_slices_passed(self) -> bool:
        report = self.observation.report
        return bool(
            report is not None
            and report.correctness is not None
            and report.correctness.passed
            and report.correctness.slices
            and all(item.passed for item in report.correctness.slices)
        )

    @property
    def all_case_floors_passed(self) -> bool:
        report = self.observation.report
        return bool(
            report is not None
            and report.performance is not None
            and report.performance.cases
            and all(item.passed_no_regression for item in report.performance.cases)
        )

    @property
    def minimum_case_speedup_vs_incumbent(self) -> float | None:
        report = self.observation.report
        if report is None or report.performance is None or not report.performance.cases:
            return None
        floors = {float(item.minimum_speedup_vs_incumbent) for item in report.performance.cases}
        return next(iter(floors)) if len(floors) == 1 else None

    @property
    def speedup_vs_incumbent(self) -> float | None:
        value = self.observation.speedup_vs_incumbent
        return float(value) if value is not None else None

    @property
    def speedup_vs_reference(self) -> float | None:
        value = self.observation.speedup_vs_reference
        return float(value) if value is not None else None

    @property
    def passed(self) -> bool:
        return self.eligible and self.all_correctness_slices_passed and self.all_case_floors_passed


class KernelWorkloadPhaseEvidence(_KernelObservedPhase):
    schema_version: Literal["autocontext.kernel-workload-phase/v1"] = "autocontext.kernel-workload-phase/v1"


class KernelTransferPhaseEvidence(_KernelObservedPhase):
    schema_version: Literal["autocontext.kernel-transfer-phase/v1"] = "autocontext.kernel-transfer-phase/v1"


def _validate_phase_pair(primary: _KernelObservedPhase, confirmation: _KernelObservedPhase) -> None:
    if primary.role != "primary" or confirmation.role != "confirmation":
        raise ValueError("evidence requires primary and confirmation roles")
    if primary.protocol_id == confirmation.protocol_id:
        raise ValueError("confirmation must use a fresh protocol")
    if primary.plan_commitment == confirmation.plan_commitment:
        raise ValueError("confirmation must use a fresh private plan")
    for name in (
        "candidate_artifact_digest",
        "candidate_source_digest",
        "incumbent_artifact_digest",
        "incumbent_source_digest",
    ):
        if getattr(primary, name) != getattr(confirmation, name):
            raise ValueError(f"confirmation {name.replace('_', ' ')} does not match primary evidence")
    if primary.observation.report is not None and confirmation.observation.report is not None:
        for name in (
            "problem_id",
            "execution_environment_id",
            "workload_family_id",
            "shape_profile_id",
            "baseline_id",
            "protocol_compatibility_id",
        ):
            if getattr(primary, name) != getattr(confirmation, name):
                raise ValueError(f"confirmation {name.replace('_', ' ')} does not match primary evidence")


class KernelWorkloadRunEvidence(StrictModel):
    """Self-contained runner, generation, and final-evaluation evidence."""

    schema_version: Literal["autocontext.kernel-workload-run/v1"] = "autocontext.kernel-workload-run/v1"
    study_execution_id: Digest
    workload_spec_id: Digest
    workload_id: str
    workload_family: str
    result: KernelEvolutionResult
    disposition: KernelWorkloadDisposition
    primary: KernelWorkloadPhaseEvidence
    confirmation: KernelWorkloadPhaseEvidence
    budget_id: Digest
    generation_budget_id: Digest
    generation_receipt_context_digest: Digest
    generation_results: tuple[KernelGenerationResult, ...]
    generation_failures: tuple[KernelGenerationFailure, ...] = ()
    budget_state: KernelGenerationBudgetState
    proposals_requested: int = Field(ge=0)
    runner_wall_seconds: PositiveFiniteFloat
    runner_cost_usd: NonNegativeFiniteFloat
    total_wall_seconds: PositiveFiniteFloat
    total_cost_usd: NonNegativeFiniteFloat
    strategy_tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        _validate_phase_pair(self.primary, self.confirmation)
        for phase in (self.primary, self.confirmation):
            if phase.candidate_artifact_digest != self.result.champion_artifact_digest:
                raise ValueError("final evidence does not measure the declared champion")
            if phase.candidate_source_digest != self.result.champion_source_digest:
                raise ValueError("final evidence does not measure the declared champion source")
        if len(set(self.strategy_tags)) != len(self.strategy_tags) or any(not item.strip() for item in self.strategy_tags):
            raise ValueError("strategy tags must be unique and non-empty")
        attempts = tuple(item for item in self.result.attempts if item.role == "candidate")
        if tuple(item.proposal_index for item in self.generation_results) != tuple(range(1, len(attempts) + 1)):
            raise ValueError("generation receipts must be contiguous and cover every proposal")
        for receipt, attempt in zip(self.generation_results, attempts, strict=True):
            if receipt.artifact_digest != attempt.artifact_digest or receipt.source_digest != attempt.source_digest:
                raise ValueError("generation receipt does not match its candidate attempt")
        if self.generation_failures:
            expected_proposal = len(self.generation_results) + 1
            if any(item.proposal_index != expected_proposal for item in self.generation_failures):
                raise ValueError("terminal generation failures must belong to the next proposal")
            if tuple(item.call_index for item in self.generation_failures) != tuple(range(1, len(self.generation_failures) + 1)):
                raise ValueError("terminal generation failure calls must be contiguous")
        expected_requested = len(self.generation_results) + bool(self.generation_failures)
        if self.proposals_requested != expected_requested:
            raise ValueError("requested proposal count disagrees with embedded generation activity")
        expected_state = KernelGenerationBudgetState.from_activity(
            self.generation_results,
            self.generation_failures,
        )
        if self.budget_state != expected_state:
            raise ValueError("generation budget state does not replay from embedded receipts")
        expected_context = kernel_generation_receipt_context_digest(
            study_execution_id=self.study_execution_id,
            workload_spec_id=self.workload_spec_id,
            run_id=self.run_id,
            generation_budget_id=self.generation_budget_id,
            generation_results=self.generation_results,
            generation_failures=self.generation_failures,
        )
        if self.generation_receipt_context_digest != expected_context:
            raise ValueError("generation receipts disagree with their study and run context commitment")
        expected_total = (
            float(self.runner_wall_seconds)
            + float(self.primary.evaluation_wall_seconds)
            + float(self.confirmation.evaluation_wall_seconds)
        )
        if float(self.total_wall_seconds) != expected_total:
            raise ValueError("workload total wall time disagrees with its measured phases")
        expected_cost = (
            float(self.runner_cost_usd) + float(self.primary.evaluation_cost_usd) + float(self.confirmation.evaluation_cost_usd)
        )
        if float(self.total_cost_usd) != expected_cost:
            raise ValueError("workload total cost disagrees with its measured phases")
        if float(self.budget_state.wall_seconds) > float(self.runner_wall_seconds):
            raise ValueError("generation wall usage cannot exceed end-to-end runner wall time")
        if float(self.budget_state.cost_usd) > float(self.runner_cost_usd):
            raise ValueError("generation cost cannot exceed end-to-end runner cost")
        return self

    @property
    def run_id(self) -> str:
        return self.result.run_id

    @property
    def result_digest(self) -> str:
        return canonical_digest(self.result.model_dump(mode="json"))

    @property
    def champion_artifact_digest(self) -> str:
        return self.result.champion_artifact_digest

    @property
    def champion_source_digest(self) -> str:
        return self.result.champion_source_digest

    @property
    def proposals_evaluated(self) -> int:
        return sum(item.role == "candidate" for item in self.result.attempts)

    @property
    def independently_verified(self) -> bool:
        return self.primary.passed and self.confirmation.passed


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
        if self.source_workload_id == self.target_workload_id and self.dimensions != ("hardware",):
            raise ValueError("same-workload transfer evidence must be hardware-only")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("transfer dimensions must be unique")
        if self.dimensions != tuple(sorted(self.dimensions)):
            raise ValueError("transfer dimensions must use canonical sorted order")
        _validate_phase_pair(self.primary, self.confirmation)
        if any(phase.candidate_artifact_digest != self.candidate_artifact_digest for phase in (self.primary, self.confirmation)):
            raise ValueError("transfer phase measures a different candidate artifact")
        return self

    @property
    def passed(self) -> bool:
        return self.primary.passed and self.confirmation.passed

    @property
    def wall_seconds(self) -> float:
        return float(self.primary.evaluation_wall_seconds) + float(self.confirmation.evaluation_wall_seconds)

    @property
    def cost_usd(self) -> float:
        return float(self.primary.evaluation_cost_usd) + float(self.confirmation.evaluation_cost_usd)


class KernelChampionTransferAssessment(StrictModel):
    candidate_artifact_digest: Digest
    source_workload_id: str
    passed_workload_ids: tuple[str, ...]
    failed_workload_ids: tuple[str, ...]
    untested_workload_ids: tuple[str, ...]
    covered_dimensions: tuple[KernelStudyDimension, ...]
    disposition: KernelTransferDisposition


class KernelWorkloadStudyReport(StrictModel):
    """Self-contained multi-workload report with replayed aggregate claims."""

    schema_version: Literal["autocontext.kernel-workload-study/v1"] = "autocontext.kernel-workload-study/v1"
    study_name: str = Field(min_length=1, max_length=256)
    created_at: str
    provenance: KernelWorkloadStudyProvenance
    workload_specs: tuple[KernelWorkloadSpec, ...] = Field(min_length=3)
    workload_runs: tuple[KernelWorkloadRunEvidence, ...] = Field(min_length=3)
    transfers: tuple[KernelTransferEvidence, ...] = ()
    protocol_burns: tuple[KernelProtocolBurn, ...]
    champion_assessments: tuple[KernelChampionTransferAssessment, ...] = ()
    portable_champion_artifact_digests: tuple[Digest, ...] = ()
    transferable_lessons: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    plateaus: tuple[str, ...] = ()
    incomplete_workloads: tuple[str, ...] = ()
    all_workloads_independently_verified: bool
    total_transfer_wall_seconds: NonNegativeFiniteFloat
    total_transfer_cost_usd: NonNegativeFiniteFloat
    total_wall_seconds: PositiveFiniteFloat
    total_cost_usd: NonNegativeFiniteFloat

    @model_validator(mode="after")
    def validate_complete_study(self, info: ValidationInfo) -> Self:
        from autocontext.kernel_evolution.workload_study_validation import validate_complete_study

        validate_complete_study(self, validation_context=info.context)
        return self

    @property
    def study_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json", exclude={"created_at"}))


def _reservation_from_observation(
    observation: KernelBenchmarkObservation,
    supplied: KernelProtocolReservation | None,
) -> KernelProtocolReservation:
    if supplied is not None:
        return supplied
    report = observation.report
    if report is None:
        raise ValueError("reportless phase evidence requires its planned protocol reservation")
    return KernelProtocolReservation(
        protocol_id=report.protocol.protocol_id,
        plan_commitment=report.protocol.seed_commitment,
        hardware_scope_id=report.hardware_scope_id,
        execution_environment_id=report.hardware.execution_environment_id,
    )


def build_kernel_workload_run_evidence(
    *,
    study_execution_id: str,
    spec: KernelWorkloadSpec,
    result: KernelEvolutionResult,
    primary_observation: KernelBenchmarkObservation,
    confirmation_observation: KernelBenchmarkObservation,
    generation_results: tuple[KernelGenerationResult, ...],
    generation_failures: tuple[KernelGenerationFailure, ...] = (),
    budget_state: KernelGenerationBudgetState,
    proposals_requested: int,
    runner_wall_seconds: float,
    runner_cost_usd: float,
    primary_evaluation_wall_seconds: float,
    confirmation_evaluation_wall_seconds: float,
    primary_evaluation_cost_usd: float,
    confirmation_evaluation_cost_usd: float,
    strategy_tags: tuple[str, ...] = (),
) -> KernelWorkloadRunEvidence:
    """Bind a completed runner result to replayable final-champion evidence."""
    disposition = workload_disposition(
        result,
        completed_proposals=len(generation_results),
        has_terminal_failures=bool(generation_failures),
        proposal_cap=spec.budget.proposal_cap,
    )
    total_wall = runner_wall_seconds + primary_evaluation_wall_seconds + confirmation_evaluation_wall_seconds
    run = KernelWorkloadRunEvidence(
        study_execution_id=study_execution_id,
        workload_spec_id=spec.spec_id,
        workload_id=spec.workload_id,
        workload_family=spec.workload_family,
        result=result,
        disposition=disposition,
        primary=KernelWorkloadPhaseEvidence(
            role="primary",
            observation=primary_observation,
            reservation=spec.final_primary_protocol,
            evaluation_wall_seconds=primary_evaluation_wall_seconds,
            evaluation_cost_usd=primary_evaluation_cost_usd,
        ),
        confirmation=KernelWorkloadPhaseEvidence(
            role="confirmation",
            observation=confirmation_observation,
            reservation=spec.final_confirmation_protocol,
            evaluation_wall_seconds=confirmation_evaluation_wall_seconds,
            evaluation_cost_usd=confirmation_evaluation_cost_usd,
        ),
        budget_id=spec.budget.budget_id,
        generation_budget_id=spec.budget.generation_budget.budget_id,
        generation_receipt_context_digest=kernel_generation_receipt_context_digest(
            study_execution_id=study_execution_id,
            workload_spec_id=spec.spec_id,
            run_id=result.run_id,
            generation_budget_id=spec.budget.generation_budget.budget_id,
            generation_results=generation_results,
            generation_failures=generation_failures,
        ),
        generation_results=generation_results,
        generation_failures=generation_failures,
        budget_state=budget_state,
        proposals_requested=proposals_requested,
        runner_wall_seconds=runner_wall_seconds,
        runner_cost_usd=runner_cost_usd,
        total_wall_seconds=total_wall,
        total_cost_usd=(runner_cost_usd + primary_evaluation_cost_usd + confirmation_evaluation_cost_usd),
        strategy_tags=strategy_tags,
    )
    validate_run_against_spec(run, spec)
    return run


def build_kernel_transfer_evidence(
    *,
    source_workload_id: str,
    target_workload_id: str,
    dimensions: tuple[KernelStudyDimension, ...],
    primary_observation: KernelBenchmarkObservation,
    confirmation_observation: KernelBenchmarkObservation,
    primary_evaluation_wall_seconds: float,
    confirmation_evaluation_wall_seconds: float,
    primary_evaluation_cost_usd: float,
    confirmation_evaluation_cost_usd: float,
    primary_reservation: KernelProtocolReservation | None = None,
    confirmation_reservation: KernelProtocolReservation | None = None,
) -> KernelTransferEvidence:
    """Build an independent cross-workload, shape, or hardware transfer receipt."""
    primary = KernelTransferPhaseEvidence(
        role="primary",
        observation=primary_observation,
        reservation=_reservation_from_observation(primary_observation, primary_reservation),
        evaluation_wall_seconds=primary_evaluation_wall_seconds,
        evaluation_cost_usd=primary_evaluation_cost_usd,
    )
    confirmation = KernelTransferPhaseEvidence(
        role="confirmation",
        observation=confirmation_observation,
        reservation=_reservation_from_observation(confirmation_observation, confirmation_reservation),
        evaluation_wall_seconds=confirmation_evaluation_wall_seconds,
        evaluation_cost_usd=confirmation_evaluation_cost_usd,
    )
    return KernelTransferEvidence(
        source_workload_id=source_workload_id,
        target_workload_id=target_workload_id,
        candidate_artifact_digest=primary.candidate_artifact_digest,
        dimensions=dimensions,
        primary=primary,
        confirmation=confirmation,
    )


def build_kernel_workload_study_report(
    *,
    study_name: str,
    provenance: KernelWorkloadStudyProvenance,
    specs: tuple[KernelWorkloadSpec, ...],
    runs: tuple[KernelWorkloadRunEvidence, ...],
    transfers: tuple[KernelTransferEvidence, ...] = (),
    created_at: str | None = None, validation_context: dict[str, object] | None = None,
) -> KernelWorkloadStudyReport:
    """Create a study report whose aggregate claims replay from embedded evidence."""
    payload = workload_study_report_payload(
        study_name=study_name,
        provenance=provenance,
        specs=specs,
        runs=runs,
        transfers=transfers,
        created_at=created_at,
    )
    return KernelWorkloadStudyReport.model_validate(payload, context=validation_context)


__all__ = [
    "KernelChampionTransferAssessment",
    "KernelProtocolBurn",
    "KernelProtocolReservation",
    "KernelStudyDimension",
    "KernelStudyEvidenceKind",
    "KernelTransferDisposition",
    "KernelTransferEvidence",
    "KernelTransferPhaseEvidence",
    "KernelTransferProtocolReservation",
    "KernelWorkloadBudget",
    "KernelWorkloadDisposition",
    "KernelWorkloadPhaseEvidence",
    "KernelWorkloadRunEvidence",
    "KernelWorkloadSpec",
    "KernelWorkloadStudyProvenance",
    "KernelWorkloadStudyReport",
    "build_kernel_transfer_evidence",
    "build_kernel_workload_run_evidence",
    "build_kernel_workload_study_report",
    "kernel_generation_receipt_context_digest",
]
