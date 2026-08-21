"""AutoContext control-plane adapter for recursive kernel improvement."""

from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.execution.agent_task_evolution import (
    AgentTaskEvolutionRunner,
    AgentTaskGenerationEvaluation,
    AgentTaskGenerationState,
    GenerateFn,
    LessonSignal,
)
from autocontext.kernel_evolution.benchmark import KernelBenchmarkEvaluator
from autocontext.kernel_evolution.confirmation import KernelConfirmationFn, evaluate_confirmation
from autocontext.kernel_evolution.lineage import KernelLineageStore
from autocontext.kernel_evolution.models import (
    ARTIFACT_IDENTITY_VERSION,
    KernelAttemptRecord,
    KernelBenchmarkObservation,
    KernelCandidate,
    KernelEvolutionResult,
    KernelPromotionDecision,
    KernelPromotionGateResult,
    kernel_benchmark_report_digest,
)
from autocontext.kernel_evolution.protocols import (
    KernelDecisionPolicy,
    KernelSequentialEvidence,
    KernelSequentialTestingPolicy,
    PrecisionProfileName,
)


class KernelBaselineError(RuntimeError):
    """Raised when the initial incumbent cannot establish a valid run scope."""

    def __init__(self, message: str, *, attempt_id: str, run_dir: Path) -> None:
        super().__init__(message)
        self.attempt_id = attempt_id
        self.run_dir = run_dir


class KernelIntegrityError(RuntimeError):
    """Raised after persisting an attempt that compromised the pinned harness."""


@dataclass(frozen=True, slots=True)
class KernelEvolutionConfig:
    problem_id: str
    task_prompt: str
    baseline_source: str
    source_suffix: str = ".py"
    entrypoint: str = "ModelNew"
    min_relative_improvement: float = 0.01
    require_confidence: bool = True
    max_p95_regression: float = 0.02
    max_environment_drift: float = 0.03
    max_peak_memory_fraction: float = 0.80
    target_reference_speedup: float = 2.0
    precision_profile: PrecisionProfileName | None = None
    proposal_cap: int | None = None
    familywise_alpha: float = 0.05

    def __post_init__(self) -> None:
        if not self.problem_id.strip() or not self.task_prompt.strip() or not self.baseline_source.strip():
            raise ValueError("problem_id, task_prompt, and baseline_source must not be empty")
        for name, value in (
            ("min_relative_improvement", self.min_relative_improvement),
            ("max_p95_regression", self.max_p95_regression),
            ("max_environment_drift", self.max_environment_drift),
            ("max_peak_memory_fraction", self.max_peak_memory_fraction),
        ):
            if not math.isfinite(value) or not 0 <= value < 1:
                raise ValueError(f"{name} must be in [0, 1)")
        if not math.isfinite(self.target_reference_speedup) or self.target_reference_speedup <= 0:
            raise ValueError("target_reference_speedup must be positive")
        if self.proposal_cap is not None and not 1 <= self.proposal_cap <= 10_000:
            raise ValueError("proposal_cap must be between 1 and 10000")
        if not math.isfinite(self.familywise_alpha) or not 0 < self.familywise_alpha < 0.5:
            raise ValueError("familywise_alpha must be in (0, 0.5)")
        KernelCandidate(source=self.baseline_source, source_suffix=self.source_suffix, entrypoint=self.entrypoint)

    @property
    def sequential_testing(self) -> KernelSequentialTestingPolicy | None:
        if self.proposal_cap is None:
            return None
        return KernelSequentialTestingPolicy(
            proposal_cap=self.proposal_cap,
            familywise_alpha=self.familywise_alpha,
        )


@dataclass(slots=True)
class _Champion:
    candidate: KernelCandidate
    observation: KernelBenchmarkObservation
    record: KernelAttemptRecord


class KernelPromotionPolicy:
    """Deterministic promotion gate, independent of the scalar prompt score."""

    _COMMON_GATE_NAMES = (
        "valid_evaluation",
        "resource_telemetry",
        "environment_drift",
        "gpu_memory",
        "case_no_regression",
        "relative_improvement",
        "tail_latency",
    )

    def __init__(self, config: KernelDecisionPolicy | KernelEvolutionConfig) -> None:
        self.config = config

    @property
    def _gate_names(self) -> tuple[str, ...]:
        finite_sample = (
            isinstance(self.config, KernelDecisionPolicy)
            and self.config.statistics.method == "paired-sign-eprocess/v1"
        )
        significance = "finite_sample_evidence" if finite_sample else "confidence_interval"
        return (*self._COMMON_GATE_NAMES[:-1], significance, self._COMMON_GATE_NAMES[-1])

    def _decision(
        self,
        *,
        promote: bool,
        decision: str,
        reason: str,
        feedback: str,
        statuses: dict[str, str],
    ) -> KernelPromotionDecision:
        gates = tuple(
            KernelPromotionGateResult(
                name=name,
                status=statuses.get(name, "not-evaluated"),  # type: ignore[arg-type]
            )
            for name in self._gate_names
        )
        summary = ", ".join(f"{gate.name}={gate.status}" for gate in gates)
        return KernelPromotionDecision(
            promote=promote,
            decision=decision,  # type: ignore[arg-type]
            reason=reason,
            feedback=f"{feedback} Gates: {summary}.",
            gates=gates,
        )

    def decide(self, observation: KernelBenchmarkObservation, *, baseline: bool = False) -> KernelPromotionDecision:
        statuses: dict[str, str] = {}
        if not observation.eligible:
            reason = observation.rejection_reason or "invalid_evaluation"
            if reason == "missing_resource_telemetry":
                statuses.update(valid_evaluation="passed", resource_telemetry="failed")
            elif reason == "resource_exceeded":
                statuses.update(valid_evaluation="passed", resource_telemetry="passed", gpu_memory="failed")
            else:
                statuses["valid_evaluation"] = "failed"
            return self._decision(
                promote=False,
                decision="rejected",
                reason=reason,
                feedback=f"{observation.feedback} Promotion veto: {reason}.",
                statuses=statuses,
            )
        statuses["valid_evaluation"] = "passed"
        resources = observation.report.resources if observation.report is not None else None
        telemetry_present = (
            resources is not None
            and all(
                value is not None
                for value in (
                    resources.candidate_artifact_digest,
                    resources.incumbent_artifact_digest,
                    resources.device_total_memory_bytes,
                )
            )
            and (
                all(
                    value is not None
                    for value in (
                        resources.candidate_peak_allocated_bytes,
                        resources.candidate_peak_reserved_bytes,
                        resources.incumbent_peak_allocated_bytes,
                        resources.incumbent_peak_reserved_bytes,
                    )
                )
                or (
                    resources.telemetry_authority == "trusted-evaluator-observed/v1"
                    and resources.accelerator_attestation_digest is not None
                    and resources.candidate_observed_peak_bytes is not None
                    and resources.incumbent_observed_peak_bytes is not None
                )
            )
        )
        if telemetry_present:
            statuses["resource_telemetry"] = "passed"
        assert observation.environment_drift_ratio is not None
        if observation.environment_drift_ratio > self.config.max_environment_drift:
            statuses["environment_drift"] = "failed"
            reason = "unstable_environment"
            feedback = (
                f"{observation.feedback} Rejected: reference drift {observation.environment_drift_ratio:.2%} exceeds "
                f"{self.config.max_environment_drift:.2%}."
            )
            return self._decision(
                promote=False,
                decision="rejected",
                reason=reason,
                feedback=feedback,
                statuses=statuses,
            )
        statuses["environment_drift"] = "passed"

        candidate_peak = resources.candidate_enforced_peak_bytes if resources is not None else None
        if (
            resources is not None
            and candidate_peak is not None
            and resources.device_total_memory_bytes is not None
            and candidate_peak > resources.device_total_memory_bytes * self.config.max_peak_memory_fraction
        ):
            statuses["gpu_memory"] = "failed"
            reason = "memory_limit"
            feedback = f"{observation.feedback} Rejected: peak device memory exceeds the configured fraction."
            return self._decision(
                promote=False,
                decision="rejected",
                reason=reason,
                feedback=feedback,
                statuses=statuses,
            )
        if candidate_peak is not None:
            statuses["gpu_memory"] = "passed"
        if baseline:
            return self._decision(
                promote=True,
                decision="baseline",
                reason="baseline",
                feedback=f"{observation.feedback} Valid baseline established.",
                statuses=statuses,
            )

        assert observation.relative_improvement is not None
        assert observation.candidate_p95_ms is not None
        assert observation.incumbent_p95_ms is not None
        if observation.all_case_no_regression_passed is False:
            statuses["case_no_regression"] = "failed"
            reason = "case_regression"
            feedback = f"{observation.feedback} Rejected: at least one protected case missed its no-regression floor."
            return self._decision(
                promote=False,
                decision="rejected",
                reason=reason,
                feedback=feedback,
                statuses=statuses,
            )
        if observation.all_case_no_regression_passed is True:
            statuses["case_no_regression"] = "passed"
        if observation.relative_improvement + 1e-12 < self.config.min_relative_improvement:
            statuses["relative_improvement"] = "failed"
            reason = "insufficient_improvement"
            feedback = (
                f"{observation.feedback} Rejected: latency improvement {observation.relative_improvement:.2%} is below "
                f"the required {self.config.min_relative_improvement:.2%}."
            )
            return self._decision(
                promote=False,
                decision="rejected",
                reason=reason,
                feedback=feedback,
                statuses=statuses,
            )
        statuses["relative_improvement"] = "passed"
        finite_sample = (
            isinstance(self.config, KernelDecisionPolicy)
            and self.config.statistics.method == "paired-sign-eprocess/v1"
        )
        if finite_sample:
            receipt = observation.derived_statistics_receipt
            if receipt is None or not receipt.finite_sample_gate_passed:
                statuses["finite_sample_evidence"] = "failed"
                return self._decision(
                    promote=False,
                    decision="rejected",
                    reason="finite_sample_evidence",
                    feedback=(
                        f"{observation.feedback} Rejected: the pre-registered finite-sample sign e-test did not "
                        "cross the per-look evidence threshold."
                    ),
                    statuses=statuses,
                )
            statuses["finite_sample_evidence"] = "passed"
        else:
            assert observation.speedup_lcb is not None
            required_confident_speedup = 1.0 / (1.0 - self.config.min_relative_improvement)
            confidence_failed = (
                observation.speedup_lcb <= 1.0
                if self.config.min_relative_improvement == 0
                else observation.speedup_lcb + 1e-12 < required_confident_speedup
            )
            if self.config.require_confidence and confidence_failed:
                statuses["confidence_interval"] = "failed"
                reason = "confidence_interval"
                feedback = (
                    f"{observation.feedback} Rejected: the empirical paired quantile does not support the configured "
                    f"{self.config.min_relative_improvement:.2%} improvement margin."
                )
                return self._decision(
                    promote=False,
                    decision="rejected",
                    reason=reason,
                    feedback=feedback,
                    statuses=statuses,
                )
            statuses["confidence_interval"] = "passed" if self.config.require_confidence else "not-evaluated"
        if observation.candidate_p95_ms > observation.incumbent_p95_ms * (1 + self.config.max_p95_regression):
            statuses["tail_latency"] = "failed"
            reason = "tail_regression"
            feedback = f"{observation.feedback} Rejected: candidate p95 latency regressed beyond the allowed limit."
            return self._decision(
                promote=False,
                decision="rejected",
                reason=reason,
                feedback=feedback,
                statuses=statuses,
            )
        statuses["tail_latency"] = "passed"
        return self._decision(
            promote=True,
            decision="promoted",
            reason="significant_improvement",
            feedback=f"{observation.feedback} Promoted: all correctness, significance, tail, drift, and resource gates passed.",
            statuses=statuses,
        )


class KernelEvolutionRunner:
    """Evolve kernel source while an immutable external harness owns truth."""

    def __init__(
        self,
        config: KernelEvolutionConfig,
        generate_fn: GenerateFn,
        evaluator: KernelBenchmarkEvaluator,
        lineage_root: Path,
        *,
        run_id: str | None = None,
        confirmation_fn: KernelConfirmationFn | None = None,
        sealed_audit_root: Path | None = None,
    ) -> None:
        if evaluator.config.problem_id != config.problem_id:
            raise ValueError("evaluator and evolution problem_id must match")
        statistics_policy = evaluator.config.statistics_policy
        finite_sample = statistics_policy.schema_version == "autocontext.kernel-statistics-policy/v2"
        if finite_sample and statistics_policy.improvement_margin != config.min_relative_improvement:
            raise ValueError("evaluator finite-sample margin and evolution promotion threshold must match")
        if finite_sample and confirmation_fn is not None and sealed_audit_root is None:
            raise ValueError("finite-sample confirmation requires a separate sealed_audit_root")
        if finite_sample and confirmation_fn is not None and sealed_audit_root is not None:
            public_root = lineage_root.resolve()
            audit_root = sealed_audit_root.resolve()
            if public_root == audit_root or public_root.is_relative_to(audit_root) or audit_root.is_relative_to(public_root):
                raise ValueError("sealed_audit_root must be disjoint from the public lineage root")
        self.config = config
        self._generate_fn = generate_fn
        self._evaluator = evaluator
        self._confirmation_fn = confirmation_fn
        self._finite_sample = finite_sample
        sequential = config.sequential_testing
        if sequential is not None:
            evaluator.config.validate_confidence_resolution(sequential.per_proposal_alpha)
        self.run_id = run_id or f"kernel_{uuid.uuid4().hex}"
        self._store = KernelLineageStore(lineage_root, self.run_id, sealed_audit_root=sealed_audit_root)
        self._decision_policy = KernelDecisionPolicy(
            schema_version=(
                "autocontext.kernel-decision-policy/v2"
                if finite_sample
                else "autocontext.kernel-decision-policy/v1"
            ),
            evidence_family_version="autocontext.kernel-evidence-family/v4" if finite_sample else None,
            statistics=statistics_policy,
            require_confirmation=confirmation_fn is not None,
            min_relative_improvement=config.min_relative_improvement,
            require_confidence=config.require_confidence,
            max_p95_regression=config.max_p95_regression,
            max_environment_drift=config.max_environment_drift,
            max_peak_memory_fraction=config.max_peak_memory_fraction,
            target_reference_speedup=config.target_reference_speedup,
            sequential_testing=config.sequential_testing,
        )
        self._policy = KernelPromotionPolicy(self._decision_policy)
        self._attempts: list[KernelAttemptRecord] = []
        self._champion: _Champion | None = None
        self._used_confirmation_protocol_ids: set[str] = set()
        self._has_run = False

    @property
    def run_dir(self) -> Path:
        return self._store.run_dir

    def _score(self, observation: KernelBenchmarkObservation) -> float:
        if not observation.eligible or observation.speedup_vs_reference is None:
            return 0.0
        return min(1.0, float(observation.speedup_vs_reference) / self.config.target_reference_speedup)

    def _new_record(
        self,
        *,
        generation: int,
        role: str,
        candidate: KernelCandidate,
        observation: KernelBenchmarkObservation,
        primary_decision: KernelPromotionDecision,
        decision: KernelPromotionDecision,
        parent: _Champion | None,
        confirmation_required: bool = False,
        confirmation_observation: KernelBenchmarkObservation | None = None,
        confirmation_decision: KernelPromotionDecision | None = None,
    ) -> KernelAttemptRecord:
        self._store.write_candidate(candidate)
        report_digest = self._store.write_report(observation)
        confirmation_report_digest = (
            kernel_benchmark_report_digest(confirmation_observation.report)
            if confirmation_observation is not None and confirmation_observation.report is not None
            else None
        )
        sequential = self.config.sequential_testing
        sequential_evidence = (
            KernelSequentialEvidence(
                proposal_index=generation,
                proposal_cap=sequential.proposal_cap,
                familywise_alpha=sequential.familywise_alpha,
                per_proposal_alpha=sequential.per_proposal_alpha,
                cumulative_alpha_spent=sequential.per_proposal_alpha * generation,
                confidence_level=sequential.confidence_level,
            )
            if role != "baseline" and sequential is not None
            else None
        )
        policy_identity = {"decision_policy_id": self._decision_policy.policy_id} if self._finite_sample else {}
        return KernelAttemptRecord(
            schema_version=("autocontext.kernel-lineage/v4" if self._finite_sample else "autocontext.kernel-lineage/v3"),
            run_id=self.run_id,
            attempt_id=f"attempt_{uuid.uuid4().hex}",
            generation=generation,
            role="baseline" if role == "baseline" else "candidate",
            artifact_identity_version=candidate.artifact_identity_version,
            artifact_digest=candidate.artifact_digest,
            source_digest=candidate.source_digest,
            report_digest=report_digest,
            source_suffix=candidate.source_suffix,
            entrypoint=candidate.entrypoint,
            parent_attempt_id=parent.record.attempt_id if parent is not None else None,
            parent_artifact_digest=parent.candidate.artifact_digest if parent is not None else None,
            decision=decision.decision,
            reason=decision.reason,
            score=self._score(observation) if observation.eligible else None,
            relative_improvement=observation.relative_improvement,
            hardware_scope_id=observation.hardware_scope_id,
            baseline_id=observation.baseline_id,
            protocol_id=observation.protocol_id,
            protocol_compatibility_id=observation.protocol_compatibility_id,
            created_at=datetime.now(UTC).isoformat(),
            observation=observation,
            decision_policy=self._decision_policy,
            **policy_identity,
            primary_decision=primary_decision,
            promotion_decision=decision,
            confirmation_required=confirmation_required,
            confirmation_report_digest=confirmation_report_digest,
            confirmation_observation=confirmation_observation,
            confirmation_decision=confirmation_decision,
            sequential_evidence=sequential_evidence,
        )

    def _confirm(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        primary_observation: KernelBenchmarkObservation,
        provisional: KernelPromotionDecision,
    ) -> tuple[KernelBenchmarkObservation | None, KernelPromotionDecision | None, KernelPromotionDecision]:
        """Run the optional fresh confirmation only after all primary gates pass."""
        return evaluate_confirmation(
            confirmation_fn=self._confirmation_fn,
            decide_fn=self._policy.decide,
            problem_id=self.config.problem_id,
            used_protocol_ids=self._used_confirmation_protocol_ids,
            candidate=candidate,
            incumbent=incumbent,
            primary_observation=primary_observation,
            provisional=provisional,
        )

    def _persist_record(self, record: KernelAttemptRecord) -> None:
        self._store.append_attempt(record)
        self._attempts.append(record)

    def _manifest(self, *, status: str, **extra: Any) -> dict[str, Any]:
        baseline = KernelCandidate(
            source=self.config.baseline_source,
            source_suffix=self.config.source_suffix,
            entrypoint=self.config.entrypoint,
        )
        return {
            "schema_version": (
                "autocontext.kernel-run/v4"
                if self._finite_sample
                else "autocontext.kernel-run/v3"
            ),
            "run_id": self.run_id,
            "status": status,
            "problem_id": self.config.problem_id,
            "artifact_identity_version": ARTIFACT_IDENTITY_VERSION,
            "baseline_artifact_digest": baseline.artifact_digest,
            "baseline_source_digest": baseline.source_digest,
            "evolution": asdict(self.config) | {"baseline_source": "stored as a content-addressed artifact"},
            "benchmark": self._evaluator.manifest(),
            "decision_policy": self._decision_policy.model_dump(mode="json"),
            "decision_policy_id": self._decision_policy.policy_id,
            "confirmation": {"enabled": self._confirmation_fn is not None},
            **extra,
        }

    def run(self, proposals: int) -> KernelEvolutionResult:
        """Evaluate the baseline, then run exactly ``proposals`` improvement attempts."""
        if proposals < 0:
            raise ValueError("proposals must be non-negative")
        if self.config.proposal_cap is not None and proposals > self.config.proposal_cap:
            raise ValueError(f"proposals ({proposals}) exceed the host-owned proposal cap ({self.config.proposal_cap})")
        if self._has_run:
            raise RuntimeError("KernelEvolutionRunner instances are single-use")
        self._has_run = True
        self._store.write_manifest(self._manifest(status="evaluating_baseline"))

        baseline = KernelCandidate(
            source=self.config.baseline_source,
            source_suffix=self.config.source_suffix,
            entrypoint=self.config.entrypoint,
        )
        baseline_observation = self._evaluator.evaluate(baseline, baseline)
        expected_sequential = self.config.sequential_testing
        report = baseline_observation.report
        observed_sequential = report.protocol.sequential_testing if report is not None else None
        observed_profile = (
            report.protocol.semantics.profile_name if report is not None and report.protocol.semantics is not None else None
        )
        controlled_protocol_matches = observed_sequential == expected_sequential and (
            self.config.precision_profile is None or observed_profile == self.config.precision_profile
        )
        if baseline_observation.eligible and not controlled_protocol_matches:
            baseline_observation = baseline_observation.model_copy(
                update={
                    "eligible": False,
                    "rejection_reason": "controlled_protocol_mismatch",
                    "feedback": "Benchmark profile or sequential-testing budget disagrees with host-owned controls.",
                }
            )
        baseline_decision = self._policy.decide(baseline_observation, baseline=True)
        baseline_record = self._new_record(
            generation=0,
            role="baseline",
            candidate=baseline,
            observation=baseline_observation,
            primary_decision=baseline_decision,
            decision=baseline_decision,
            parent=None,
        )
        self._persist_record(baseline_record)
        if not baseline_decision.promote:
            self._store.write_manifest(self._manifest(status="baseline_failed", baseline_attempt_id=baseline_record.attempt_id))
            raise KernelBaselineError(
                baseline_decision.feedback,
                attempt_id=baseline_record.attempt_id,
                run_dir=self.run_dir,
            )
        self._champion = _Champion(baseline, baseline_observation, baseline_record)
        self._store.write_champion(baseline, baseline_record)
        assert baseline_observation.hardware_scope_id is not None
        assert baseline_observation.baseline_id is not None
        assert baseline_observation.protocol_id is not None
        assert baseline_observation.protocol_compatibility_id is not None
        self._store.write_manifest(
            self._manifest(
                status="running",
                baseline_attempt_id=baseline_record.attempt_id,
                hardware_scope_id=baseline_observation.hardware_scope_id,
                baseline_id=baseline_observation.baseline_id,
                protocol_id=baseline_observation.protocol_id,
                protocol_compatibility_id=baseline_observation.protocol_compatibility_id,
            )
        )

        def evaluate_source(source: str, _generation: int) -> AgentTaskGenerationEvaluation:
            assert self._champion is not None
            candidate = KernelCandidate(
                source=source,
                source_suffix=self.config.source_suffix,
                entrypoint=self.config.entrypoint,
            )
            observation = self._evaluator.evaluate(
                candidate,
                self._champion.candidate,
                expected_scope_id=baseline_observation.hardware_scope_id,
                expected_baseline_id=baseline_observation.baseline_id,
                expected_protocol_id=baseline_observation.protocol_id,
            )
            provisional_decision = self._policy.decide(observation)
            confirmation_observation, confirmation_decision, decision = self._confirm(
                candidate,
                self._champion.candidate,
                observation,
                provisional_decision,
            )
            aggregate_feedback = self._evaluator.config.adaptive_feedback_policy == "aggregate-gates"
            if aggregate_feedback:
                gate_status = {gate.name: gate.status for gate in provisional_decision.gates}
                disclosed_feedback = (
                    "Aggregate benchmark gates: "
                    + ", ".join(f"{gate.name}={gate.status}" for gate in provisional_decision.gates)
                    + f". Disposition={provisional_decision.reason}."
                )
                metrics: dict[str, float] = {}
                performance_dimension = float(gate_status.get("relative_improvement") == "passed")
                adaptive_score = float(provisional_decision.promote)
            else:
                disclosed_feedback = provisional_decision.feedback
                metrics = {}
                if observation.relative_improvement is not None:
                    metrics["relative_improvement"] = float(observation.relative_improvement)
                if observation.speedup_lcb is not None:
                    metrics["speedup_lcb"] = float(observation.speedup_lcb)
                performance_dimension = min(1.0, float(observation.speedup_vs_incumbent or 0.0))
                adaptive_score = self._score(observation)
            return AgentTaskGenerationEvaluation(
                output=source,
                score=adaptive_score,
                # Confirmation evidence is persisted for audit, but its
                # detailed feedback and metrics must not become training data
                # for later adaptive proposals.
                reasoning=disclosed_feedback,
                dimension_scores={
                    "correctness": 1.0 if observation.eligible else 0.0,
                    "performance": performance_dimension,
                    "promotion_gate": 1.0 if provisional_decision.promote else 0.0,
                },
                met_threshold=decision.promote,
                lesson_signal=LessonSignal(
                    hint=disclosed_feedback,
                    plateau=provisional_decision.reason in {"insufficient_improvement", "confidence_interval"},
                    metrics=metrics,
                ),
                metadata={
                    "candidate": candidate,
                    "observation": observation,
                    "primary_decision": provisional_decision,
                    "decision": decision,
                    "confirmation_observation": confirmation_observation,
                    "confirmation_decision": confirmation_decision,
                },
            )

        def promote(state: AgentTaskGenerationState, evaluation: AgentTaskGenerationEvaluation) -> bool:
            assert self._champion is not None
            candidate = evaluation.metadata.get("candidate")
            observation = evaluation.metadata.get("observation")
            primary_decision = evaluation.metadata.get("primary_decision")
            decision = evaluation.metadata.get("decision")
            confirmation_observation = evaluation.metadata.get("confirmation_observation")
            confirmation_decision = evaluation.metadata.get("confirmation_decision")
            if not isinstance(candidate, KernelCandidate):
                raise TypeError("kernel evaluation metadata is missing candidate")
            if not isinstance(observation, KernelBenchmarkObservation):
                raise TypeError("kernel evaluation metadata is missing observation")
            if not isinstance(decision, KernelPromotionDecision):
                raise TypeError("kernel evaluation metadata is missing promotion decision")
            if not isinstance(primary_decision, KernelPromotionDecision):
                raise TypeError("kernel evaluation metadata is missing primary promotion decision")
            if confirmation_observation is not None and not isinstance(confirmation_observation, KernelBenchmarkObservation):
                raise TypeError("kernel evaluation metadata contains an invalid confirmation observation")
            if confirmation_decision is not None and not isinstance(confirmation_decision, KernelPromotionDecision):
                raise TypeError("kernel evaluation metadata contains an invalid confirmation decision")
            parent = self._champion
            record = self._new_record(
                generation=state.generation + 1,
                role="candidate",
                candidate=candidate,
                observation=observation,
                primary_decision=primary_decision,
                decision=decision,
                parent=parent,
                confirmation_required=self._confirmation_fn is not None,
                confirmation_observation=confirmation_observation,
                confirmation_decision=confirmation_decision,
            )
            self._persist_record(record)
            if decision.reason in {"harness_modified", "confirmation_harness_modified"}:
                raise KernelIntegrityError(decision.feedback)
            if decision.promote:
                self._champion = _Champion(candidate, observation, record)
                self._store.write_champion(candidate, record)
            return decision.promote

        baseline_score = self._score(baseline_observation)
        state = AgentTaskGenerationState(
            generation=0,
            best_output=baseline.source,
            best_score=baseline_score,
            playbook="",
            score_history=[baseline_score],
            lesson_history=[],
            metadata={},
        )
        evolution = AgentTaskEvolutionRunner(
            task_prompt=self.config.task_prompt,
            generate_fn=self._generate_fn,
            evaluate_fn=evaluate_source,
            task_name=f"kernel:{self.config.problem_id}",
            promotion_fn=promote,
        )
        try:
            for _ in range(proposals):
                state = evolution.run_generation(state)
        except BaseException as exc:
            assert self._champion is not None
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            try:
                self._store.write_manifest(
                    self._manifest(
                        status=status,
                        hardware_scope_id=baseline_observation.hardware_scope_id,
                        baseline_id=baseline_observation.baseline_id,
                        protocol_id=baseline_observation.protocol_id,
                        protocol_compatibility_id=baseline_observation.protocol_compatibility_id,
                        champion_attempt_id=self._champion.record.attempt_id,
                        attempts=len(self._attempts),
                        error_type=type(exc).__name__,
                        error=(
                            "terminal failure; detailed confirmation evidence remains in sealed audit"
                            if self._finite_sample and self._confirmation_fn is not None
                            else str(exc)[:1_000]
                        ),
                    )
                )
            except Exception:
                pass
            try:
                self._store.release_sealed_audit()
            except Exception:
                pass
            raise

        assert self._champion is not None
        champion_speedup = self._champion.observation.speedup_vs_reference
        assert champion_speedup is not None
        policy_id = self._decision_policy.policy_id if self._finite_sample else None
        result = KernelEvolutionResult(
            schema_version=("autocontext.kernel-result/v4" if self._finite_sample else "autocontext.kernel-result/v3"),
            run_id=self.run_id,
            problem_id=self.config.problem_id,
            hardware_scope_id=baseline_observation.hardware_scope_id,
            baseline_id=baseline_observation.baseline_id,
            protocol_id=baseline_observation.protocol_id,
            protocol_compatibility_id=baseline_observation.protocol_compatibility_id,
            precision_profile=observed_profile,
            baseline_attempt_id=baseline_record.attempt_id,
            champion_attempt_id=self._champion.record.attempt_id,
            artifact_identity_version=self._champion.candidate.artifact_identity_version,
            champion_artifact_digest=self._champion.candidate.artifact_digest,
            champion_source_digest=self._champion.candidate.source_digest,
            champion_source=self._champion.candidate.source,
            champion_score=self._score(self._champion.observation),
            champion_speedup_vs_reference=champion_speedup,
            decision_policy=self._decision_policy,
            **({"decision_policy_id": policy_id} if policy_id is not None else {}),
            attempts=list(self._attempts),
            playbook=state.playbook,
        )
        self._store.release_sealed_audit()
        self._store.write_summary(result)
        self._store.write_manifest(
            self._manifest(
                status="complete",
                hardware_scope_id=result.hardware_scope_id,
                baseline_id=result.baseline_id,
                protocol_id=result.protocol_id,
                protocol_compatibility_id=result.protocol_compatibility_id,
                champion_attempt_id=result.champion_attempt_id,
                attempts=len(result.attempts),
            )
        )
        return result
