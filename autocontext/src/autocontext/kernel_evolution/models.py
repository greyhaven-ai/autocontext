"""Typed contracts for correctness-first kernel evolution."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from autocontext.kernel_evolution.authority_protocol import (
    KernelEvaluatorAuthorityReceipt,
    verify_authority_receipt,
)
from autocontext.kernel_evolution.protocols import (
    KernelDecisionPolicy,
    KernelProtocolSemantics,
    KernelSequentialEvidence,
    KernelSequentialTestingPolicy,
    KernelStatisticsPolicy,
    PrecisionProfileName,
)
from autocontext.kernel_evolution.report_models import (
    KernelCasePerformanceReport as KernelCasePerformanceReport,
)
from autocontext.kernel_evolution.report_models import (
    KernelCorrectnessReport,
    KernelPerformanceReport,
)
from autocontext.kernel_evolution.report_models import (
    KernelCorrectnessSliceReport as KernelCorrectnessSliceReport,
)
from autocontext.kernel_evolution.report_models import (
    KernelTimingBlock as KernelTimingBlock,
)

SCHEMA_VERSION: Literal["autocontext.kernelbench-eval/v3"] = "autocontext.kernelbench-eval/v3"
ARTIFACT_IDENTITY_VERSION: Literal["autocontext.kernel-artifact/v2"] = "autocontext.kernel-artifact/v2"
PROTOCOL_COMPATIBILITY_VERSION: Literal["autocontext.kernel-protocol-compatibility/v1"] = (
    "autocontext.kernel-protocol-compatibility/v1"
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
ArtifactIdentityVersion = Literal["autocontext.kernel-artifact/v2"]
ProtocolCompatibilityVersion = Literal["autocontext.kernel-protocol-compatibility/v1"]
PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0)]
ConfidenceLevel = Annotated[FiniteFloat, Field(gt=0.5, lt=1)]


def content_digest(content: str | bytes) -> str:
    """Return the canonical branded SHA-256 digest for source or bytes."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def canonical_digest(payload: dict[str, Any]) -> str:
    """Hash a mapping using stable JSON encoding."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return content_digest(encoded)


def artifact_digest_from_source_digest(
    source_digest: str,
    *,
    source_suffix: str,
    entrypoint: str,
) -> str:
    """Bind exact source bytes and their executable ABI into one v2 identity.

    ``source_digest`` remains separately persisted so file-integrity checks and
    legacy source-only evidence do not have to overload the ABI-bound identity.
    """
    if not _DIGEST_PATTERN.fullmatch(source_digest):
        raise ValueError("source_digest must be a branded SHA-256 digest")
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", source_suffix):
        raise ValueError("source_suffix must be a short extension such as '.py' or '.cu'")
    if not entrypoint.strip():
        raise ValueError("entrypoint must not be empty")
    return canonical_digest(
        {
            "identity_version": ARTIFACT_IDENTITY_VERSION,
            "source_digest": source_digest,
            "source_suffix": source_suffix,
            "entrypoint": entrypoint,
        }
    )


def artifact_digest(source: str | bytes, *, source_suffix: str, entrypoint: str) -> str:
    """Return the shared v2 ABI-bound identity for exact UTF-8/source bytes."""
    return artifact_digest_from_source_digest(
        content_digest(source),
        source_suffix=source_suffix,
        entrypoint=entrypoint,
    )


def _require_finite_json(value: Any, *, path: str = "metadata") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_finite_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            _require_finite_json(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value of type {type(value).__name__}")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class KernelCandidate(StrictModel):
    """An exact source artifact supplied to the external benchmark."""

    source: str
    source_suffix: str = ".py"
    entrypoint: str = "ModelNew"
    artifact_identity_version: ArtifactIdentityVersion = ARTIFACT_IDENTITY_VERSION

    @model_validator(mode="after")
    def validate_suffix(self) -> Self:
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", self.source_suffix):
            raise ValueError("source_suffix must be a short extension such as '.py' or '.cu'")
        if not self.source.strip():
            raise ValueError("kernel source must not be empty")
        if not self.entrypoint.strip():
            raise ValueError("entrypoint must not be empty")
        return self

    @property
    def source_bytes(self) -> bytes:
        return self.source.encode("utf-8")

    @property
    def source_digest(self) -> str:
        return content_digest(self.source_bytes)

    @property
    def artifact_digest(self) -> str:
        return artifact_digest_from_source_digest(
            self.source_digest,
            source_suffix=self.source_suffix,
            entrypoint=self.entrypoint,
        )


class KernelHardwareIdentity(StrictModel):
    """Hardware, software, and pinned workload identity for one timing scope."""

    backend: str
    architecture: str
    device_name: str
    runtime: str
    driver: str
    toolchain: str
    workload_family_id: Digest
    workload_fingerprint: Digest
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_non_empty_identity(self) -> Self:
        required = {
            "backend": self.backend,
            "architecture": self.architecture,
            "device_name": self.device_name,
            "runtime": self.runtime,
            "driver": self.driver,
            "toolchain": self.toolchain,
        }
        empty = sorted(name for name, value in required.items() if not value.strip())
        if empty:
            raise ValueError(f"hardware identity fields must not be empty: {', '.join(empty)}")
        return self

    @property
    def scope_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))

    @property
    def execution_environment_id(self) -> str:
        """Identity of the execution stack, excluding the protocol-bound workload fingerprint."""
        return canonical_digest(
            {
                "backend": self.backend,
                "architecture": self.architecture,
                "device_name": self.device_name,
                "runtime": self.runtime,
                "driver": self.driver,
                "toolchain": self.toolchain,
                "metadata": self.metadata,
            }
        )


class KernelBenchmarkProtocol(StrictModel):
    """Pinned correctness and timing settings owned by the benchmark harness."""

    correctness_trials: int = Field(ge=1)
    hidden_trials: int = Field(ge=1)
    warmup_runs: int = Field(ge=0)
    timing_blocks: int = Field(ge=1)
    calls_per_block: int = Field(ge=1)
    atol: NonNegativeFiniteFloat
    rtol: NonNegativeFiniteFloat
    seed_commitment: Digest
    compatibility_version: ProtocolCompatibilityVersion = PROTOCOL_COMPATIBILITY_VERSION
    semantics: KernelProtocolSemantics | None = None
    sequential_testing: KernelSequentialTestingPolicy | None = None

    @model_validator(mode="after")
    def validate_hidden_trials(self) -> Self:
        if self.hidden_trials > self.correctness_trials:
            raise ValueError("hidden_trials cannot exceed correctness_trials")
        if self.semantics is not None:
            expected = 0.0001 if self.semantics.profile_name == "strict-fp32-v1" else 0.01
            if abs(float(self.atol) - expected) > 1e-15 or abs(float(self.rtol) - expected) > 1e-15:
                raise ValueError("tolerances do not match the named precision profile")
        return self

    @property
    def protocol_id(self) -> str:
        # Preserve the v1 protocol-id semantics for historical evidence. The
        # new compatibility version is domain separation for the family only.
        return canonical_digest(self.model_dump(mode="json", exclude={"compatibility_version"}, exclude_none=True))

    @property
    def compatibility_id(self) -> str:
        """Identity shared only by protocols whose non-random semantics match."""
        return canonical_digest(
            {
                "compatibility_version": self.compatibility_version,
                **self.model_dump(
                    mode="json",
                    exclude={"compatibility_version", "seed_commitment"},
                    exclude_none=True,
                ),
            }
        )


class KernelCompileReport(StrictModel):
    candidate_passed: bool
    incumbent_passed: bool
    candidate_compile_ms: NonNegativeFiniteFloat | None = None
    diagnostics: str = ""


class KernelResourceReport(StrictModel):
    """Identity-bound allocator or evaluator-observed accelerator telemetry."""

    candidate_artifact_digest: Digest | None = None
    incumbent_artifact_digest: Digest | None = None
    candidate_peak_allocated_bytes: int | None = Field(default=None, ge=0)
    candidate_peak_reserved_bytes: int | None = Field(default=None, ge=0)
    incumbent_peak_allocated_bytes: int | None = Field(default=None, ge=0)
    incumbent_peak_reserved_bytes: int | None = Field(default=None, ge=0)
    # Retained for v2 readers; new workers set these to peak reservation.
    candidate_peak_memory_bytes: int | None = Field(default=None, ge=0)
    incumbent_peak_memory_bytes: int | None = Field(default=None, ge=0)
    candidate_observed_peak_bytes: int | None = Field(default=None, ge=0)
    incumbent_observed_peak_bytes: int | None = Field(default=None, ge=0)
    telemetry_authority: Literal["trusted-evaluator-observed/v1"] | None = None
    accelerator_attestation_digest: Digest | None = None
    device_total_memory_bytes: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_cuda_peaks(self) -> Self:
        for role in ("candidate", "incumbent"):
            allocated = getattr(self, f"{role}_peak_allocated_bytes")
            reserved = getattr(self, f"{role}_peak_reserved_bytes")
            if allocated is not None and reserved is not None and allocated > reserved:
                raise ValueError(f"{role} peak allocated bytes cannot exceed peak reserved bytes")
        if self.device_total_memory_bytes is not None:
            for value in (
                self.candidate_peak_allocated_bytes,
                self.candidate_peak_reserved_bytes,
                self.incumbent_peak_allocated_bytes,
                self.incumbent_peak_reserved_bytes,
                self.candidate_observed_peak_bytes,
                self.incumbent_observed_peak_bytes,
            ):
                if value is not None and value > self.device_total_memory_bytes:
                    raise ValueError("accelerator peak telemetry cannot exceed total device bytes")
        observed = (self.candidate_observed_peak_bytes, self.incumbent_observed_peak_bytes)
        if any(value is not None for value in observed):
            if any(value is None for value in observed):
                raise ValueError("trusted evaluator peaks must cover candidate and incumbent")
            if self.telemetry_authority != "trusted-evaluator-observed/v1":
                raise ValueError("evaluator-observed peaks require the trusted telemetry authority")
            if self.accelerator_attestation_digest is None:
                raise ValueError("evaluator-observed peaks require an accelerator attestation digest")
        return self

    @property
    def candidate_enforced_peak_bytes(self) -> int | None:
        values = (
            self.candidate_peak_allocated_bytes,
            self.candidate_peak_reserved_bytes,
            self.candidate_peak_memory_bytes,
            self.candidate_observed_peak_bytes,
        )
        present = [value for value in values if value is not None]
        return max(present) if present else None


KernelEvaluationStatus = Literal["complete", "candidate_error", "infrastructure_error"]
KernelFailureKind = Literal[
    "syntax",
    "compile",
    "correctness",
    "timeout",
    "oom",
    "crash",
    "unstable_environment",
    "contract",
    "protocol_corruption",
    "evaluator_crash",
    "candidate_crash",
    "teardown_failure",
    "reference_failure",
]


class KernelBenchmarkReport(StrictModel):
    """Machine-written benchmark result. AutoContext recomputes every score."""

    schema_version: Literal["autocontext.kernelbench-eval/v2", "autocontext.kernelbench-eval/v3"] = SCHEMA_VERSION
    evaluation_status: KernelEvaluationStatus
    failure_kind: KernelFailureKind | None = None
    problem_id: str
    artifact_identity_version: ArtifactIdentityVersion
    candidate_artifact_digest: Digest
    incumbent_artifact_digest: Digest
    candidate_source_digest: Digest
    incumbent_source_digest: Digest
    candidate_source_suffix: str
    incumbent_source_suffix: str
    candidate_entrypoint: str
    incumbent_entrypoint: str
    baseline_id: Digest
    hardware: KernelHardwareIdentity
    hardware_scope_id: Digest
    protocol: KernelBenchmarkProtocol
    compile: KernelCompileReport
    correctness: KernelCorrectnessReport | None
    performance: KernelPerformanceReport | None
    resources: KernelResourceReport = Field(default_factory=KernelResourceReport)
    evaluator_authority_receipt: KernelEvaluatorAuthorityReceipt | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        _require_finite_json(self.metadata)
        if not self.candidate_entrypoint.strip() or not self.incumbent_entrypoint.strip():
            raise ValueError("candidate and incumbent entrypoints must not be empty")
        expected_candidate = artifact_digest_from_source_digest(
            self.candidate_source_digest,
            source_suffix=self.candidate_source_suffix,
            entrypoint=self.candidate_entrypoint,
        )
        expected_incumbent = artifact_digest_from_source_digest(
            self.incumbent_source_digest,
            source_suffix=self.incumbent_source_suffix,
            entrypoint=self.incumbent_entrypoint,
        )
        if self.candidate_artifact_digest != expected_candidate:
            raise ValueError("candidate artifact digest does not match its source digest and ABI")
        if self.incumbent_artifact_digest != expected_incumbent:
            raise ValueError("incumbent artifact digest does not match its source digest and ABI")
        if self.hardware_scope_id != self.hardware.scope_id:
            raise ValueError("hardware_scope_id does not match the canonical hardware identity")
        if self.evaluator_authority_receipt is not None:
            verify_authority_receipt(self.evaluator_authority_receipt, self.model_dump(mode="json"))
            attestation = self.evaluator_authority_receipt.accelerator_attestation
            if attestation.backend != self.hardware.backend or attestation.architecture != self.hardware.architecture:
                raise ValueError("authority receipt accelerator identity does not match report hardware")
            if self.resources.accelerator_attestation_digest != attestation.digest:
                raise ValueError("resource telemetry is not bound to the receipt accelerator attestation")
            if self.resources.device_total_memory_bytes != attestation.enforced_memory_bytes:
                raise ValueError("resource telemetry capacity does not match receipt accelerator attestation")
        resources = self.resources
        if (
            resources.candidate_artifact_digest is not None
            and resources.candidate_artifact_digest != self.candidate_artifact_digest
        ):
            raise ValueError("candidate resource telemetry is bound to a different artifact")
        if (
            resources.incumbent_artifact_digest is not None
            and resources.incumbent_artifact_digest != self.incumbent_artifact_digest
        ):
            raise ValueError("incumbent resource telemetry is bound to a different artifact")
        if self.correctness is not None:
            if self.correctness.tests_run != self.protocol.correctness_trials:
                raise ValueError("correctness tests_run does not match protocol correctness_trials")
            if self.correctness.hidden_tests_run != self.protocol.hidden_trials:
                raise ValueError("correctness hidden_tests_run does not match protocol hidden_trials")
        if self.evaluation_status == "complete":
            if self.failure_kind is not None:
                raise ValueError("complete reports cannot include failure_kind")
            if not self.compile.candidate_passed or not self.compile.incumbent_passed:
                raise ValueError("complete reports require successful candidate and incumbent compilation")
            if self.correctness is None or not self.correctness.passed:
                raise ValueError("complete reports require passed correctness")
            if self.performance is None:
                raise ValueError("complete reports require paired performance blocks")
            if len(self.performance.blocks) != self.protocol.timing_blocks:
                raise ValueError("protocol timing_blocks does not match the performance block count")
            semantics = self.protocol.semantics
            if semantics is not None:
                enforcement = semantics.enforcement
                assert self.correctness is not None
                if enforcement.require_every_correctness_slice:
                    if not self.correctness.slices:
                        raise ValueError("semantic protocols require named correctness slices")
                    observed_splits = {item.split for item in self.correctness.slices}
                    if observed_splits != set(semantics.inputs.required_slices):
                        raise ValueError("correctness slices do not cover the protocol's required splits")
                if enforcement.require_every_case_no_regression:
                    if not self.performance.cases:
                        raise ValueError("semantic protocols require per-case performance gates")
                    correctness_cases = [(item.name, item.split) for item in self.correctness.slices]
                    performance_cases = [(item.name, item.split) for item in self.performance.cases]
                    if len(set(correctness_cases)) != len(correctness_cases):
                        raise ValueError("correctness slice names and splits must be unique")
                    if len(set(performance_cases)) != len(performance_cases):
                        raise ValueError("performance case names and splits must be unique")
                    if set(performance_cases) != set(correctness_cases):
                        raise ValueError("performance cases must cover every named correctness slice")
                    for case in self.performance.cases:
                        if (
                            abs(float(case.minimum_speedup_vs_incumbent) - float(enforcement.minimum_case_speedup_vs_incumbent))
                            > 1e-12
                        ):
                            raise ValueError("case no-regression floor does not match the protocol")
        else:
            if self.failure_kind is None:
                raise ValueError("failed reports require failure_kind")
            if self.performance is not None:
                raise ValueError("failed reports cannot include performance measurements")
        return self


class KernelBenchmarkObservation(StrictModel):
    """Consumer-validated result and statistics derived from a raw report."""

    artifact_identity_version: ArtifactIdentityVersion
    candidate_artifact_digest: Digest
    incumbent_artifact_digest: Digest
    candidate_source_digest: Digest
    incumbent_source_digest: Digest
    eligible: bool
    rejection_reason: str | None = None
    feedback: str
    report: KernelBenchmarkReport | None = None
    hardware_scope_id: Digest | None = None
    baseline_id: Digest | None = None
    protocol_id: Digest | None = None
    protocol_compatibility_id: Digest | None = None
    statistics_policy: KernelStatisticsPolicy | None = None
    candidate_median_ms: PositiveFiniteFloat | None = None
    incumbent_median_ms: PositiveFiniteFloat | None = None
    reference_median_ms: PositiveFiniteFloat | None = None
    speedup_vs_incumbent: PositiveFiniteFloat | None = None
    speedup_vs_reference: PositiveFiniteFloat | None = None
    speedup_lcb95: PositiveFiniteFloat | None = None
    speedup_lcb: PositiveFiniteFloat | None = None
    confidence_level: ConfidenceLevel | None = None
    all_case_no_regression_passed: bool | None = None
    relative_improvement: FiniteFloat | None = None
    candidate_p95_ms: PositiveFiniteFloat | None = None
    incumbent_p95_ms: PositiveFiniteFloat | None = None
    environment_drift_ratio: NonNegativeFiniteFloat | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @model_validator(mode="before")
    @classmethod
    def migrate_pre_sequential_v2_metrics(cls, data: Any) -> Any:
        """Read eligible v2 observations written before sequential bounds.

        Those observations persisted the 95% lower bound under
        ``speedup_lcb95``.  It is safe to map that value to the generic field
        only when the embedded protocol has no sequential-testing policy; a
        bounded-search observation must carry its adjusted bound explicitly.
        """
        if not isinstance(data, dict) or not data.get("eligible"):
            return data
        if data.get("speedup_lcb") is not None or data.get("confidence_level") is not None:
            return data
        lcb95 = data.get("speedup_lcb95")
        report = data.get("report")
        if lcb95 is None or report is None:
            return data
        if isinstance(report, BaseModel):
            protocol = getattr(report, "protocol", None)
            sequential = getattr(protocol, "sequential_testing", None)
        elif isinstance(report, dict):
            protocol = report.get("protocol")
            sequential = protocol.get("sequential_testing") if isinstance(protocol, dict) else None
        else:
            return data
        if sequential is not None:
            return data
        migrated = dict(data)
        migrated["speedup_lcb"] = lcb95
        migrated["confidence_level"] = 0.95
        return migrated

    @model_validator(mode="after")
    def validate_eligible_metrics(self) -> Self:
        metrics = (
            self.candidate_median_ms,
            self.incumbent_median_ms,
            self.reference_median_ms,
            self.speedup_vs_incumbent,
            self.speedup_vs_reference,
            self.speedup_lcb95,
            self.speedup_lcb,
            self.confidence_level,
            self.relative_improvement,
            self.candidate_p95_ms,
            self.incumbent_p95_ms,
            self.environment_drift_ratio,
        )
        if self.eligible and (
            self.report is None
            or self.protocol_id is None
            or self.protocol_compatibility_id is None
            or any(value is None for value in metrics)
        ):
            raise ValueError("eligible observations require a report, protocol identities, and all derived metrics")
        if self.eligible and self.rejection_reason is not None:
            raise ValueError("eligible observations cannot have a rejection_reason")
        if not self.eligible and not self.rejection_reason:
            raise ValueError("ineligible observations require a rejection_reason")
        if self.report is not None:
            report = self.report
            if report.schema_version == "autocontext.kernelbench-eval/v3" and self.statistics_policy is None:
                raise ValueError("v3 eligible observations require a statistics-policy receipt")
            if self.eligible and (
                self.artifact_identity_version != report.artifact_identity_version
                or self.candidate_artifact_digest != report.candidate_artifact_digest
                or self.incumbent_artifact_digest != report.incumbent_artifact_digest
                or self.candidate_source_digest != report.candidate_source_digest
                or self.incumbent_source_digest != report.incumbent_source_digest
            ):
                raise ValueError("eligible observation artifact identity does not match its report")
            if self.hardware_scope_id != report.hardware_scope_id or self.baseline_id != report.baseline_id:
                raise ValueError("observation scope or baseline does not match its report")
            if self.protocol_id != report.protocol.protocol_id:
                raise ValueError("observation protocol id does not match its report")
            if self.protocol_compatibility_id != report.protocol.compatibility_id:
                raise ValueError("observation protocol compatibility id does not match its report")
            if self.eligible:
                assert self.confidence_level is not None
                sequential = report.protocol.sequential_testing
                expected_confidence = sequential.confidence_level if sequential is not None else 0.95
                if abs(float(self.confidence_level) - expected_confidence) > 1e-15:
                    raise ValueError("observation confidence_level disagrees with its benchmark protocol")
        return self


KernelDecision = Literal["baseline", "promoted", "rejected"]
KernelGateStatus = Literal["passed", "failed", "not-evaluated"]


class KernelPromotionGateResult(StrictModel):
    name: str
    status: KernelGateStatus
    detail: str = ""


class KernelPromotionDecision(StrictModel):
    promote: bool
    decision: KernelDecision
    reason: str
    feedback: str
    gates: tuple[KernelPromotionGateResult, ...] = ()

    @model_validator(mode="after")
    def validate_promotion_flag(self) -> Self:
        if self.promote != (self.decision in {"baseline", "promoted"}):
            raise ValueError("promotion decision flag and disposition disagree")
        return self


def kernel_benchmark_report_digest(report: KernelBenchmarkReport) -> str:
    """Digest the exact canonical report representation persisted by lineage."""
    return content_digest(report.model_dump_json(indent=2))


class KernelAttemptRecord(StrictModel):
    """Append-only lineage record for one baseline or proposal evaluation."""

    schema_version: Literal["autocontext.kernel-lineage/v2", "autocontext.kernel-lineage/v3"] = (
        "autocontext.kernel-lineage/v3"
    )
    run_id: str
    attempt_id: str
    generation: int = Field(ge=0)
    role: Literal["baseline", "candidate"]
    artifact_identity_version: ArtifactIdentityVersion
    artifact_digest: Digest
    source_digest: Digest
    report_digest: Digest | None
    source_suffix: str
    entrypoint: str
    parent_attempt_id: str | None
    parent_artifact_digest: Digest | None
    decision: KernelDecision
    reason: str
    score: FiniteFloat | None
    relative_improvement: FiniteFloat | None
    hardware_scope_id: Digest | None
    baseline_id: Digest | None
    protocol_id: Digest | None
    protocol_compatibility_id: Digest | None
    created_at: str
    observation: KernelBenchmarkObservation
    decision_policy: KernelDecisionPolicy | None = None
    primary_decision: KernelPromotionDecision | None = None
    promotion_decision: KernelPromotionDecision | None = None
    confirmation_required: bool = False
    confirmation_report_digest: Digest | None = None
    confirmation_observation: KernelBenchmarkObservation | None = None
    confirmation_decision: KernelPromotionDecision | None = None
    sequential_evidence: KernelSequentialEvidence | None = None

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        from autocontext.kernel_evolution.evidence_replay import validate_attempt

        validate_attempt(self)
        return self


class KernelEvolutionResult(StrictModel):
    """Stable result surface that reports the champion, not the final attempt."""

    schema_version: Literal["autocontext.kernel-result/v2", "autocontext.kernel-result/v3"] = (
        "autocontext.kernel-result/v3"
    )
    run_id: str
    problem_id: str
    hardware_scope_id: Digest
    baseline_id: Digest
    protocol_id: Digest
    protocol_compatibility_id: Digest
    precision_profile: PrecisionProfileName | None = None
    baseline_attempt_id: str
    champion_attempt_id: str
    artifact_identity_version: ArtifactIdentityVersion
    champion_artifact_digest: Digest
    champion_source_digest: Digest
    champion_source: str
    champion_score: PositiveFiniteFloat
    champion_speedup_vs_reference: PositiveFiniteFloat
    decision_policy: KernelDecisionPolicy | None = None
    attempts: list[KernelAttemptRecord]
    playbook: str

    @model_validator(mode="after")
    def validate_result_lineage(self) -> Self:
        from autocontext.kernel_evolution.evidence_replay import validate_result

        validate_result(self)
        return self


def require_digest(value: str, *, name: str) -> str:
    """Validate a digest outside Pydantic model construction."""
    if not _DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a branded SHA-256 digest")
    return value


def finite_positive(values: list[float]) -> bool:
    """Small helper for adapters that construct reports from untyped data."""
    return bool(values) and all(math.isfinite(value) and value > 0 for value in values)
