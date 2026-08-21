"""AutoContext control-plane adapter for recursive kernel improvement."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
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
from autocontext.kernel_evolution.lineage import KernelLineageStore
from autocontext.kernel_evolution.models import (
    ARTIFACT_IDENTITY_VERSION,
    KernelAttemptRecord,
    KernelBenchmarkObservation,
    KernelCandidate,
    KernelEvolutionResult,
    KernelPromotionDecision,
    KernelPromotionGateResult,
)


class KernelBaselineError(RuntimeError):
    """Raised when the initial incumbent cannot establish a valid run scope."""

    def __init__(self, message: str, *, attempt_id: str, run_dir: Path) -> None:
        super().__init__(message)
        self.attempt_id = attempt_id
        self.run_dir = run_dir


class KernelIntegrityError(RuntimeError):
    """Raised after persisting an attempt that compromised the pinned harness."""


KernelConfirmationFn = Callable[
    [KernelCandidate, KernelCandidate],
    KernelBenchmarkObservation | None,
]


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
        KernelCandidate(source=self.baseline_source, source_suffix=self.source_suffix, entrypoint=self.entrypoint)


@dataclass(slots=True)
class _Champion:
    candidate: KernelCandidate
    observation: KernelBenchmarkObservation
    record: KernelAttemptRecord


class KernelPromotionPolicy:
    """Deterministic promotion gate, independent of the scalar prompt score."""

    _GATE_NAMES = (
        "valid_evaluation",
        "resource_telemetry",
        "environment_drift",
        "gpu_memory",
        "relative_improvement",
        "confidence_interval",
        "tail_latency",
    )

    def __init__(self, config: KernelEvolutionConfig) -> None:
        self.config = config

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
            for name in self._GATE_NAMES
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
        telemetry_present = resources is not None and all(
            value is not None
            for value in (
                resources.candidate_artifact_digest,
                resources.incumbent_artifact_digest,
                resources.candidate_peak_allocated_bytes,
                resources.candidate_peak_reserved_bytes,
                resources.incumbent_peak_allocated_bytes,
                resources.incumbent_peak_reserved_bytes,
                resources.device_total_memory_bytes,
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
        assert observation.speedup_lcb95 is not None
        assert observation.candidate_p95_ms is not None
        assert observation.incumbent_p95_ms is not None
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
        required_confident_speedup = 1.0 / (1.0 - self.config.min_relative_improvement)
        confidence_failed = (
            observation.speedup_lcb95 <= 1.0
            if self.config.min_relative_improvement == 0
            else observation.speedup_lcb95 + 1e-12 < required_confident_speedup
        )
        if self.config.require_confidence and confidence_failed:
            statuses["confidence_interval"] = "failed"
            reason = "confidence_interval"
            feedback = (
                f"{observation.feedback} Rejected: the paired 95% lower bound does not support the configured "
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
    ) -> None:
        if evaluator.config.problem_id != config.problem_id:
            raise ValueError("evaluator and evolution problem_id must match")
        self.config = config
        self._generate_fn = generate_fn
        self._evaluator = evaluator
        self._confirmation_fn = confirmation_fn
        self.run_id = run_id or f"kernel_{uuid.uuid4().hex}"
        self._store = KernelLineageStore(lineage_root, self.run_id)
        self._policy = KernelPromotionPolicy(config)
        self._attempts: list[KernelAttemptRecord] = []
        self._champion: _Champion | None = None
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
        decision: KernelPromotionDecision,
        parent: _Champion | None,
        confirmation_required: bool = False,
        confirmation_observation: KernelBenchmarkObservation | None = None,
        confirmation_decision: KernelPromotionDecision | None = None,
    ) -> KernelAttemptRecord:
        self._store.write_candidate(candidate)
        report_digest = self._store.write_report(observation)
        confirmation_report_digest = (
            self._store.write_report(confirmation_observation) if confirmation_observation is not None else None
        )
        return KernelAttemptRecord(
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
            confirmation_required=confirmation_required,
            confirmation_report_digest=confirmation_report_digest,
            confirmation_observation=confirmation_observation,
            confirmation_decision=confirmation_decision,
        )

    @staticmethod
    def _confirmation_veto(
        provisional: KernelPromotionDecision,
        confirmation: KernelPromotionDecision,
    ) -> KernelPromotionDecision:
        confirmation_gates = tuple(gate.model_copy(update={"name": f"confirmation.{gate.name}"}) for gate in confirmation.gates)
        return KernelPromotionDecision(
            promote=False,
            decision="rejected",
            reason=f"confirmation_{confirmation.reason}",
            feedback=f"{provisional.feedback} Independent confirmation veto: {confirmation.feedback}",
            gates=(*provisional.gates, *confirmation_gates),
        )

    def _confirm(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        primary_observation: KernelBenchmarkObservation,
        provisional: KernelPromotionDecision,
    ) -> tuple[KernelBenchmarkObservation | None, KernelPromotionDecision | None, KernelPromotionDecision]:
        """Run the optional fresh confirmation only after all primary gates pass."""
        if self._confirmation_fn is None or not provisional.promote:
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
            observation = self._confirmation_fn(candidate, incumbent)
        except Exception as exc:
            confirmation = reject(
                "error",
                f"Confirmation evaluator failed: {type(exc).__name__}: {str(exc)[:1_000]}",
            )
            return None, confirmation, self._confirmation_veto(provisional, confirmation)
        if observation is None:
            confirmation = reject("missing", "Confirmation evaluator returned no observation.")
            return None, confirmation, self._confirmation_veto(provisional, confirmation)
        if not isinstance(observation, KernelBenchmarkObservation):
            confirmation = reject("invalid", "Confirmation evaluator returned an invalid observation type.")
            return None, confirmation, self._confirmation_veto(provisional, confirmation)

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
            return audited_observation, confirmation, self._confirmation_veto(provisional, confirmation)
        if report is None:
            confirmation = self._policy.decide(observation)
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
        if report is not None and report.problem_id != self.config.problem_id:
            confirmation = reject("problem_mismatch", "Confirmation used a different kernel problem.")
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
        if observation.baseline_id != primary_observation.baseline_id:
            confirmation = reject("baseline_mismatch", "Confirmation used a different reference baseline.")
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
        if report is not None and (
            observation.hardware_scope_id != report.hardware_scope_id
            or observation.baseline_id != report.baseline_id
            or observation.protocol_id != report.protocol.protocol_id
            or observation.protocol_compatibility_id != report.protocol.compatibility_id
        ):
            confirmation = reject("contract_mismatch", "Confirmation observation disagrees with its benchmark report.")
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
        primary_report = primary_observation.report
        if primary_report is not None and report.hardware.workload_family_id != primary_report.hardware.workload_family_id:
            confirmation = reject(
                "workload_mismatch",
                "Confirmation changed the static shape, dtype, reference, or input contract.",
            )
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
        if primary_report is not None and (
            report.hardware.execution_environment_id != primary_report.hardware.execution_environment_id
        ):
            confirmation = reject(
                "environment_mismatch",
                "Confirmation used a different backend, device, runtime, driver, or toolchain.",
            )
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
        if observation.protocol_id == primary_observation.protocol_id:
            confirmation = reject("not_fresh", "Confirmation reused the primary benchmark protocol.")
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
        if observation.protocol_compatibility_id != primary_observation.protocol_compatibility_id:
            confirmation = reject(
                "protocol_incompatible",
                "Confirmation changed correctness, tolerance, trial-count, warmup, or timing semantics.",
            )
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)

        confirmation = self._policy.decide(observation)
        if not confirmation.promote:
            return observation, confirmation, self._confirmation_veto(provisional, confirmation)
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
            "schema_version": "autocontext.kernel-run/v2",
            "run_id": self.run_id,
            "status": status,
            "problem_id": self.config.problem_id,
            "artifact_identity_version": ARTIFACT_IDENTITY_VERSION,
            "baseline_artifact_digest": baseline.artifact_digest,
            "baseline_source_digest": baseline.source_digest,
            "evolution": asdict(self.config) | {"baseline_source": "stored as a content-addressed artifact"},
            "benchmark": self._evaluator.manifest(),
            "confirmation": {"enabled": self._confirmation_fn is not None},
            **extra,
        }

    def run(self, proposals: int) -> KernelEvolutionResult:
        """Evaluate the baseline, then run exactly ``proposals`` improvement attempts."""
        if proposals < 0:
            raise ValueError("proposals must be non-negative")
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
        baseline_decision = self._policy.decide(baseline_observation, baseline=True)
        baseline_record = self._new_record(
            generation=0,
            role="baseline",
            candidate=baseline,
            observation=baseline_observation,
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
            metrics: dict[str, float] = {}
            if observation.relative_improvement is not None:
                metrics["relative_improvement"] = float(observation.relative_improvement)
            if observation.speedup_lcb95 is not None:
                metrics["speedup_lcb95"] = float(observation.speedup_lcb95)
            performance_dimension = min(1.0, float(observation.speedup_vs_incumbent or 0.0))
            return AgentTaskGenerationEvaluation(
                output=source,
                score=self._score(observation),
                reasoning=decision.feedback,
                dimension_scores={
                    "correctness": 1.0 if observation.eligible else 0.0,
                    "performance": performance_dimension,
                    "promotion_gate": 1.0 if decision.promote else 0.0,
                },
                met_threshold=decision.promote,
                lesson_signal=LessonSignal(
                    hint=decision.feedback,
                    plateau=decision.reason.removeprefix("confirmation_") in {"insufficient_improvement", "confidence_interval"},
                    metrics=metrics,
                ),
                metadata={
                    "candidate": candidate,
                    "observation": observation,
                    "decision": decision,
                    "confirmation_observation": confirmation_observation,
                    "confirmation_decision": confirmation_decision,
                },
            )

        def promote(state: AgentTaskGenerationState, evaluation: AgentTaskGenerationEvaluation) -> bool:
            assert self._champion is not None
            candidate = evaluation.metadata.get("candidate")
            observation = evaluation.metadata.get("observation")
            decision = evaluation.metadata.get("decision")
            confirmation_observation = evaluation.metadata.get("confirmation_observation")
            confirmation_decision = evaluation.metadata.get("confirmation_decision")
            if not isinstance(candidate, KernelCandidate):
                raise TypeError("kernel evaluation metadata is missing candidate")
            if not isinstance(observation, KernelBenchmarkObservation):
                raise TypeError("kernel evaluation metadata is missing observation")
            if not isinstance(decision, KernelPromotionDecision):
                raise TypeError("kernel evaluation metadata is missing promotion decision")
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
                        error=str(exc)[:1_000],
                    )
                )
            except Exception:
                pass
            raise

        assert self._champion is not None
        champion_speedup = self._champion.observation.speedup_vs_reference
        assert champion_speedup is not None
        result = KernelEvolutionResult(
            run_id=self.run_id,
            problem_id=self.config.problem_id,
            hardware_scope_id=baseline_observation.hardware_scope_id,
            baseline_id=baseline_observation.baseline_id,
            protocol_id=baseline_observation.protocol_id,
            protocol_compatibility_id=baseline_observation.protocol_compatibility_id,
            baseline_attempt_id=baseline_record.attempt_id,
            champion_attempt_id=self._champion.record.attempt_id,
            artifact_identity_version=self._champion.candidate.artifact_identity_version,
            champion_artifact_digest=self._champion.candidate.artifact_digest,
            champion_source_digest=self._champion.candidate.source_digest,
            champion_source=self._champion.candidate.source,
            champion_score=self._score(self._champion.observation),
            champion_speedup_vs_reference=champion_speedup,
            attempts=list(self._attempts),
            playbook=state.playbook,
        )
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
