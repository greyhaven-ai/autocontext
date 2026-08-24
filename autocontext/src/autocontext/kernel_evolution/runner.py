"""AutoContext control-plane adapter for recursive kernel improvement."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.kernel_evolution import promotion_margin
from autocontext.kernel_evolution.adaptive_evidence import (
    validate_sealed_evidence_roots,
)
from autocontext.kernel_evolution.benchmark import KernelBenchmarkEvaluator
from autocontext.kernel_evolution.campaign_journal import (
    KernelCampaignAmbiguousExecution,
    KernelCampaignJournal,
)
from autocontext.kernel_evolution.confirmation import KernelConfirmationFn, evaluate_confirmation
from autocontext.kernel_evolution.generation import (
    KernelGenerateFn,
    KernelGenerationBudget,
    KernelGenerationBudgetExceeded,
    KernelGenerationCancelled,
    KernelGenerationProviderError,
    KernelGenerationResult,
    KernelGenerationUsage,
    validate_kernel_source,
)
from autocontext.kernel_evolution.lineage import KernelLineageStore
from autocontext.kernel_evolution.models import (
    ARTIFACT_IDENTITY_VERSION,
    KernelAttemptRecord,
    KernelBenchmarkObservation,
    KernelCandidate,
    KernelEvolutionResult,
    KernelPromotionDecision,
    KernelPromotionGateResult,
    canonical_digest,
    content_digest,
    kernel_benchmark_report_digest,
)
from autocontext.kernel_evolution.protocols import (
    KernelDecisionPolicy,
    KernelSequentialEvidence,
)
from autocontext.kernel_evolution.runner_config import KernelEvolutionConfig
from autocontext.util.file_lock import advisory_path_lock


class KernelBaselineError(RuntimeError):
    """Raised when the initial incumbent cannot establish a valid run scope."""

    def __init__(self, message: str, *, attempt_id: str, run_dir: Path) -> None:
        super().__init__(message)
        self.attempt_id = attempt_id
        self.run_dir = run_dir


class KernelIntegrityError(RuntimeError):
    """Raised after persisting an attempt that compromised the pinned harness."""


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

    def _uses_finite_sample(self) -> bool:
        config = self.config
        return isinstance(config, KernelDecisionPolicy) and config.statistics.method == "paired-sign-eprocess/v1"
    @property
    def _gate_names(self) -> tuple[str, ...]:
        finite_sample = self._uses_finite_sample()
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
        finite_sample = self._uses_finite_sample()
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
        if not promotion_margin.environment_drift_margin_passed(
            observation, self.config.max_environment_drift, finite_sample=finite_sample
        ):
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
            and not promotion_margin.peak_memory_fraction_passed(
                candidate_peak, resources.device_total_memory_bytes, self.config.max_peak_memory_fraction,
                finite_sample=finite_sample,
            )
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
        aggregate_margin_passed = (
            observation.relative_improvement + 1e-12 >= self.config.min_relative_improvement
        )
        if finite_sample:
            aggregate_margin_passed = promotion_margin.finite_sample_aggregate_margin_passed(
                observation,
                self.config.min_relative_improvement,
            )
        if not aggregate_margin_passed:
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
        tail_margin_passed = (
            observation.candidate_p95_ms
            <= observation.incumbent_p95_ms * (1 + self.config.max_p95_regression)
        )
        if finite_sample:
            tail_margin_passed = promotion_margin.finite_sample_tail_margin_passed(
                observation,
                self.config.max_p95_regression,
            )
        if not tail_margin_passed:
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
        generate_fn: KernelGenerateFn,
        evaluator: KernelBenchmarkEvaluator,
        lineage_root: Path,
        *,
        run_id: str | None = None,
        confirmation_fn: KernelConfirmationFn | None = None,
        sealed_audit_root: Path | None = None,
        generation_budget: KernelGenerationBudget | None = None,
        confirmation_identity: str | None = None,
        resume: bool = False,
    ) -> None:
        if evaluator.config.problem_id != config.problem_id:
            raise ValueError("evaluator and evolution problem_id must match")
        statistics_policy = evaluator.config.statistics_policy
        finite_sample = statistics_policy.schema_version == "autocontext.kernel-statistics-policy/v2"
        quarantine_primary_evidence = (
            finite_sample and evaluator.config.adaptive_feedback_policy == "aggregate-gates"
        )
        if finite_sample and statistics_policy.improvement_margin != config.min_relative_improvement:
            raise ValueError("evaluator finite-sample margin and evolution promotion threshold must match")
        validate_sealed_evidence_roots(
            finite_sample=finite_sample,
            confirmation_enabled=confirmation_fn is not None,
            quarantine_primary_evidence=quarantine_primary_evidence,
            public_root=lineage_root,
            sealed_audit_root=sealed_audit_root,
        )
        self.config = config
        self._generate_fn = generate_fn
        self._evaluator = evaluator
        self._confirmation_fn = confirmation_fn
        self._finite_sample = finite_sample
        self._quarantine_primary_evidence = quarantine_primary_evidence
        sequential = config.sequential_testing
        if sequential is not None:
            evaluator.config.validate_confidence_resolution(sequential.per_proposal_alpha)
        self.run_id = run_id or f"kernel_{uuid.uuid4().hex}"
        if resume and run_id is None:
            raise ValueError("resuming a kernel campaign requires its stable run_id")
        proposal_cap = config.proposal_cap or 10_000
        generator_budget = getattr(generate_fn, "budget", None)
        self._generation_budget = generation_budget or (
            generator_budget
            if isinstance(generator_budget, KernelGenerationBudget)
            else KernelGenerationBudget(proposal_cap=proposal_cap)
        )
        if config.proposal_cap is not None and self._generation_budget.proposal_cap != config.proposal_cap:
            raise ValueError("generation and evaluation proposal caps must match")
        if (
            isinstance(generator_budget, KernelGenerationBudget)
            and generator_budget.budget_id != self._generation_budget.budget_id
        ):
            raise ValueError("runner and generator generation budgets must match")
        generator_budget_id = getattr(generate_fn, "generation_budget_id", None)
        if (
            generator_budget_id is not None
            and generator_budget_id != self._generation_budget.budget_id
        ):
            raise ValueError("runner and generator generation budget identities must match")
        if confirmation_identity is not None and not confirmation_identity.strip():
            raise ValueError("confirmation_identity must not be empty")
        self._confirmation_identity = confirmation_identity or (
            self._callable_identity(confirmation_fn) if confirmation_fn is not None else None
        )
        self._generator_identity = self._build_generator_identity()
        self._resume = resume
        self._store = KernelLineageStore(
            lineage_root,
            self.run_id,
            sealed_audit_root=sealed_audit_root,
            quarantine_primary_evidence=quarantine_primary_evidence,
            resume=resume,
        )
        self._journal = KernelCampaignJournal(self._store.run_dir, self.run_id)
        set_call_observer = getattr(self._generate_fn, "set_call_observer", None)
        set_failure_observer = getattr(self._generate_fn, "set_failure_observer", None)
        self._call_fence_resume_safe = bool(
            getattr(self._generate_fn, "supports_durable_call_fence", False) is True
            and callable(set_call_observer)
            and callable(set_failure_observer)
            and callable(getattr(self._generate_fn, "restore_pending_failures", None))
        )
        if callable(set_call_observer):
            set_call_observer(self._journal.claim_generation_call)
        if callable(set_failure_observer):
            set_failure_observer(self._journal.write_generation_failure)
        restored_generations = self._journal.generation_results() if resume else []
        self._generation_results = {item.proposal_index: item for item in restored_generations}
        self._execution_lock_path = lineage_root / ".kernel-execution-locks" / f"{self.run_id}.lock"
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
        self._used_confirmation_evidence_ids: set[str] = set()
        self._has_run = False

    @property
    def run_dir(self) -> Path:
        return self._store.run_dir

    @property
    def attempts(self) -> tuple[KernelAttemptRecord, ...]:
        """Attempts durably reconstructed or persisted by the active campaign."""
        return tuple(self._attempts)

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
        attempt_id: str | None = None,
    ) -> KernelAttemptRecord:
        self._store.write_candidate(candidate)
        report_digest = self._store.write_report(
            observation,
            publish=not self._quarantine_primary_evidence,
        )
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
            attempt_id=attempt_id or f"attempt_{uuid.uuid4().hex}",
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
            used_evidence_ids=self._used_confirmation_evidence_ids,
            candidate=candidate,
            incumbent=incumbent,
            primary_observation=primary_observation,
            provisional=provisional,
        )

    def _persist_record(self, record: KernelAttemptRecord) -> None:
        self._store.append_attempt(record)
        self._attempts.append(record)
        if record.role == "candidate":
            generation = self._generation_results.get(record.generation)
            if generation is None:
                raise KernelIntegrityError("candidate attempt has no durable generation receipt")
            self._journal.link_attempt(
                generation,
                attempt_id=record.attempt_id,
                artifact_digest=record.artifact_digest,
            )
        self._journal.refresh_artifact_index()

    def _generate_source(self, prompt: str, generation: int) -> str:
        proposal_index = generation + 1
        existing = self._journal.read_generation_result(proposal_index)
        if existing is not None:
            if existing.prompt_digest != content_digest(prompt.encode("utf-8")):
                raise KernelIntegrityError("resumed generation prompt does not match its durable receipt")
            self._generation_results[proposal_index] = existing
            self._require_generation_budget()
            return existing.source
        if self._journal.stop_requested():
            raise KernelGenerationCancelled("kernel campaign stop requested before provider dispatch")
        if proposal_index > self._generation_budget.proposal_cap:
            raise KernelGenerationBudgetExceeded("kernel generation proposal cap is exhausted")
        system_prompt = getattr(self._generate_fn, "system_prompt", None)
        existing_claim = self._journal.read_generation_claim(proposal_index)
        claim_resume_safe = bool(getattr(self._generate_fn, "supports_claim_resume", False))
        if existing_claim is not None:
            _, unresolved_calls = self._journal.generation_call_state(proposal_index)
            resume_safe = not unresolved_calls and (
                self._call_fence_resume_safe or claim_resume_safe
            )
            if (
                not resume_safe
                or existing_claim.prompt_digest != content_digest(prompt.encode("utf-8"))
                or existing_claim.generator_identity != self._generator_identity
            ):
                raise KernelCampaignAmbiguousExecution(
                    f"proposal {proposal_index} has an unresolved generation claim and will not be repeated"
                )
        else:
            self._journal.claim_generation(
                proposal_index=proposal_index,
                prompt=prompt,
                generator_identity=self._generator_identity,
                system_prompt=system_prompt if isinstance(system_prompt, str) else None,
            )
        try:
            generated = self._generate_fn(prompt, generation)
        except KernelGenerationCancelled as exc:
            self._journal.write_generation_cancellation(
                proposal_index,
                tuple(getattr(exc, "failures", ())),
            )
            raise
        except KernelGenerationProviderError as exc:
            self._journal.write_terminal_failures(proposal_index, exc.failures, outcome="provider_error")
            raise
        except KernelGenerationBudgetExceeded as exc:
            if exc.result is not None:
                self._store.write_candidate(
                    KernelCandidate(
                        source=exc.result.source,
                        source_suffix=exc.result.source_suffix,
                        entrypoint=exc.result.entrypoint,
                    )
                )
                self._journal.write_generation_result(exc.result)
                self._generation_results[proposal_index] = exc.result
            self._journal.write_terminal_failures(proposal_index, exc.failures, outcome="budget_exceeded")
            raise
        if isinstance(generated, KernelGenerationResult):
            result = generated
            validate_kernel_source(
                result.source,
                source_suffix=result.source_suffix,
                entrypoint=result.entrypoint,
                stop_reason=result.stop_reason,
                max_source_bytes=self._generation_budget.max_source_bytes,
            )
        else:
            candidate = KernelCandidate(
                source=generated,
                source_suffix=self.config.source_suffix,
                entrypoint=self.config.entrypoint,
            )
            result = KernelGenerationResult(
                proposal_index=proposal_index,
                provider="callable",
                model=self._callable_identity(),
                system_prompt_digest=content_digest(b"legacy callable generation adapter"),
                prompt_digest=content_digest(prompt.encode("utf-8")),
                response_digest=content_digest(generated.encode("utf-8")),
                source_digest=candidate.source_digest,
                artifact_digest=candidate.artifact_digest,
                source=generated,
                source_suffix=self.config.source_suffix,
                entrypoint=self.config.entrypoint,
                usage=KernelGenerationUsage(),
                cost_usd=0.0,
                cost_source="not-billable",
                latency_seconds=0.0,
                retry_count=0,
                completed_at=datetime.now(UTC).isoformat(),
            )
        if (
            result.proposal_index != proposal_index
            or result.prompt_digest != content_digest(prompt.encode("utf-8"))
            or result.source_suffix != self.config.source_suffix
            or result.entrypoint != self.config.entrypoint
        ):
            raise KernelIntegrityError("generated source receipt conflicts with the active proposal contract")
        from autocontext.kernel_evolution.runner_resume import validate_generation_budget_contract

        validate_generation_budget_contract(self, (result,))
        candidate = KernelCandidate(
            source=result.source,
            source_suffix=result.source_suffix,
            entrypoint=result.entrypoint,
        )
        self._store.write_candidate(candidate)
        self._journal.write_generation_result(result)
        self._generation_results[proposal_index] = result
        try:
            self._require_generation_budget()
        except KernelGenerationBudgetExceeded as exc:
            self._journal.write_terminal_failures(
                proposal_index,
                exc.failures,
                outcome="budget_exceeded",
            )
            raise
        if self._journal.stop_requested():
            raise KernelGenerationCancelled(
                "kernel campaign stop requested after provider completion; generated source was preserved without GPU work"
            )
        return result.source

    def _require_generation_budget(self) -> None:
        state = self._journal.budget_state()
        exceeded = []
        if state.input_tokens > self._generation_budget.max_total_input_tokens:
            exceeded.append("input_tokens")
        if state.output_tokens > self._generation_budget.max_total_output_tokens:
            exceeded.append("output_tokens")
        if state.total_tokens > self._generation_budget.max_total_tokens:
            exceeded.append("total_tokens")
        if float(state.cost_usd) > float(self._generation_budget.max_cost_usd):
            exceeded.append("cost_usd")
        if float(state.wall_seconds) > float(self._generation_budget.max_wall_seconds):
            exceeded.append("wall_seconds")
        if exceeded:
            raise KernelGenerationBudgetExceeded(
                f"kernel generation budget exceeded: {', '.join(exceeded)}",
                result=(
                    self._generation_results[max(self._generation_results)]
                    if self._generation_results
                    else None
                ),
            )

    def _build_generator_identity(self) -> str:
        system_prompt = getattr(self._generate_fn, "system_prompt", None)
        return canonical_digest(
            {
                "kind": "kernel-generator-identity/v1",
                "callable": self._callable_identity(),
                "provider": getattr(self._generate_fn, "provider_id", None),
                "model": getattr(self._generate_fn, "model", None),
                "transport_identity": getattr(self._generate_fn, "transport_identity", None),
                "system_prompt_digest": (
                    content_digest(system_prompt.encode("utf-8"))
                    if isinstance(system_prompt, str)
                    else None
                ),
                "temperature": getattr(self._generate_fn, "temperature", None),
                "source_suffix": getattr(self._generate_fn, "source_suffix", None),
                "entrypoint": getattr(self._generate_fn, "entrypoint", None),
                "generation_budget_id": self._generation_budget.budget_id,
            }
        )

    def _callable_identity(self, function: Any | None = None) -> str:
        target = self._generate_fn if function is None else function
        module = getattr(target, "__module__", None)
        qualname = getattr(target, "__qualname__", None)
        if isinstance(module, str) and isinstance(qualname, str):
            return f"{module}.{qualname}"
        return f"{type(target).__module__}.{type(target).__qualname__}"

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
            "evolution": self._evolution_contract(),
            "benchmark": self._evaluator.manifest(),
            "decision_policy": self._decision_policy.model_dump(mode="json"),
            "decision_policy_id": self._decision_policy.policy_id,
            "confirmation": self._confirmation_contract(),
            "generation": {
                "generator_identity": self._generator_identity,
                "claim_resume_safe": bool(
                    getattr(self._generate_fn, "supports_claim_resume", False)
                ),
                "call_fence_resume_safe": self._call_fence_resume_safe,
                "budget_id": self._generation_budget.budget_id,
                "budget": self._generation_budget.model_dump(mode="json"),
                "budget_state": self._journal.budget_state().model_dump(mode="json"),
            },
            **extra,
        }

    def _evolution_contract(self) -> dict[str, Any]:
        return asdict(self.config) | {"baseline_source": "stored as a content-addressed artifact"}

    def _confirmation_contract(self) -> dict[str, Any]:
        return {
            "enabled": self._confirmation_fn is not None,
            "identity": self._confirmation_identity,
        }

    def run(self, proposals: int) -> KernelEvolutionResult:
        from autocontext.kernel_evolution.runner_lifecycle import run_kernel_evolution

        with advisory_path_lock(self._execution_lock_path):
            return run_kernel_evolution(self, proposals)
