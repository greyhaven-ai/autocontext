"""Single-use kernel evolution lifecycle extracted from the public runner."""

from __future__ import annotations

from typing import Any

from autocontext.execution.agent_task_evolution import (
    AgentTaskEvolutionRunner,
    AgentTaskGenerationEvaluation,
    AgentTaskGenerationState,
    LessonSignal,
)
from autocontext.kernel_evolution.adaptive_evidence import (
    confirmation_identity_unavailable,
    release_sealed_audit_best_effort,
    terminal_error_text,
)
from autocontext.kernel_evolution.generation import KernelGenerationCancelled
from autocontext.kernel_evolution.models import (
    KernelBenchmarkObservation,
    KernelCandidate,
    KernelEvolutionResult,
    KernelPromotionDecision,
)
from autocontext.kernel_evolution.runner_resume import restore_kernel_run


def run_kernel_evolution(runner: Any, proposals: int) -> KernelEvolutionResult:
    """Evaluate the baseline, then run exactly ``proposals`` improvement attempts."""
    from autocontext.kernel_evolution.runner import KernelBaselineError, KernelIntegrityError, _Champion

    if proposals < 0:
        raise ValueError("proposals must be non-negative")
    if runner.config.proposal_cap is not None and proposals > runner.config.proposal_cap:
        raise ValueError(f"proposals ({proposals}) exceed the host-owned proposal cap ({runner.config.proposal_cap})")
    if runner._has_run:
        raise RuntimeError("KernelEvolutionRunner instances are single-use")
    runner._has_run = True
    if not runner._resume and (runner.run_dir / "manifest.json").exists():
        raise FileExistsError(f"kernel run already started: {runner.run_dir}")
    if proposals > runner._generation_budget.proposal_cap:
        raise ValueError(
            f"proposals ({proposals}) exceed the generation proposal cap "
            f"({runner._generation_budget.proposal_cap})"
        )
    restored = restore_kernel_run(runner, proposals=proposals)
    if runner._resume and runner._store.read_manifest().get("status") == "complete":
        summary = runner._store.read_summary()
        if not isinstance(summary, KernelEvolutionResult):
            raise ValueError("complete kernel campaign is missing its summary")
        return summary
    baseline = KernelCandidate(
        source=runner.config.baseline_source,
        source_suffix=runner.config.source_suffix,
        entrypoint=runner.config.entrypoint,
    )
    if restored is None:
        runner._store.write_manifest(
            runner._manifest(status="evaluating_baseline", proposals_requested=proposals)
        )
        runner._journal.refresh_artifact_index()
        with runner._journal.begin_evaluation(
            generation=0,
            role="baseline",
            artifact_digest=baseline.artifact_digest,
        ) as baseline_claim:
            baseline_observation = runner._evaluator.evaluate(baseline, baseline)
        expected_sequential = runner.config.sequential_testing
        report = baseline_observation.report
        observed_sequential = report.protocol.sequential_testing if report is not None else None
        observed_profile = (
            report.protocol.semantics.profile_name
            if report is not None and report.protocol.semantics is not None
            else None
        )
        controlled_protocol_matches = observed_sequential == expected_sequential and (
            runner.config.precision_profile is None or observed_profile == runner.config.precision_profile
        )
        if baseline_observation.eligible and not controlled_protocol_matches:
            rejected_payload = baseline_observation.model_dump(mode="python")
            rejected_payload.update(
                eligible=False,
                rejection_reason="controlled_protocol_mismatch",
                feedback="Benchmark profile or sequential-testing budget disagrees with host-owned controls.",
                derived_statistics_receipt=None,
            )
            baseline_observation = KernelBenchmarkObservation.model_validate(rejected_payload)
        baseline_decision = runner._policy.decide(baseline_observation, baseline=True)
        baseline_record = runner._new_record(
            generation=0,
            role="baseline",
            candidate=baseline,
            observation=baseline_observation,
            primary_decision=baseline_decision,
            decision=baseline_decision,
            parent=None,
            attempt_id=baseline_claim.attempt_id,
        )
        runner._persist_record(baseline_record)
        if not baseline_decision.promote:
            runner._store.write_manifest(
                runner._manifest(
                    status="baseline_failed",
                    proposals_requested=proposals,
                    baseline_attempt_id=baseline_record.attempt_id,
                )
            )
            release_sealed_audit_best_effort(runner._store)
            runner._journal.refresh_artifact_index()
            raise KernelBaselineError(
                baseline_decision.feedback,
                attempt_id=baseline_record.attempt_id,
                run_dir=runner.run_dir,
            )
        runner._champion = _Champion(baseline, baseline_observation, baseline_record)
        runner._store.write_champion(baseline, baseline_record)
        baseline_score = runner._score(baseline_observation)
        state = AgentTaskGenerationState(
            generation=0,
            best_output=baseline.source,
            best_score=baseline_score,
            playbook="",
            score_history=[baseline_score],
            lesson_history=[],
            metadata={},
        )
    else:
        _restored_champion, baseline_record, state, observed_profile = restored
        baseline = runner._store.read_candidate(baseline_record)
        baseline_observation = baseline_record.observation

    assert runner._champion is not None
    assert baseline_observation.hardware_scope_id is not None
    assert baseline_observation.baseline_id is not None
    assert baseline_observation.protocol_id is not None
    assert baseline_observation.protocol_compatibility_id is not None
    runner._store.write_manifest(
        runner._manifest(
            status="running",
            proposals_requested=proposals,
            baseline_attempt_id=baseline_record.attempt_id,
            hardware_scope_id=baseline_observation.hardware_scope_id,
            baseline_id=baseline_observation.baseline_id,
            protocol_id=baseline_observation.protocol_id,
            protocol_compatibility_id=baseline_observation.protocol_compatibility_id,
            champion_attempt_id=runner._champion.record.attempt_id,
            attempts=len(runner._attempts),
        )
    )
    runner._journal.refresh_artifact_index()

    def evaluate_source(source: str, _generation: int) -> AgentTaskGenerationEvaluation:
        assert runner._champion is not None
        proposal_index = _generation + 1
        generation_result = runner._generation_results.get(proposal_index)
        if generation_result is None:
            raise KernelIntegrityError("candidate evaluation has no durable generation receipt")
        candidate = KernelCandidate(
            source=source,
            source_suffix=runner.config.source_suffix,
            entrypoint=runner.config.entrypoint,
        )
        if candidate.artifact_digest != generation_result.artifact_digest:
            raise KernelIntegrityError("candidate source changed after its generation receipt was persisted")
        if runner._journal.stop_requested():
            raise KernelGenerationCancelled(
                "kernel campaign stop requested after source generation and before GPU evaluation"
            )
        with runner._journal.begin_evaluation(
            generation=proposal_index,
            role="candidate",
            artifact_digest=candidate.artifact_digest,
            generation_receipt_id=generation_result.receipt_id,
        ) as evaluation_claim:
            observation = runner._evaluator.evaluate(
                candidate,
                runner._champion.candidate,
                expected_scope_id=baseline_observation.hardware_scope_id,
                expected_baseline_id=baseline_observation.baseline_id,
                expected_protocol_id=baseline_observation.protocol_id,
            )
            provisional_decision = runner._policy.decide(observation)
            if runner._confirmation_fn is not None and runner._journal.stop_requested():
                raise KernelGenerationCancelled(
                    "kernel campaign stop requested after primary evaluation and before confirmation"
                )
            confirmation_observation, confirmation_decision, decision = runner._confirm(
                candidate,
                runner._champion.candidate,
                observation,
                provisional_decision,
            )
        aggregate_feedback = runner._evaluator.config.adaptive_feedback_policy == "aggregate-gates"
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
            adaptive_score = runner._score(observation)
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
                "attempt_id": evaluation_claim.attempt_id,
            },
        )

    def promote(state: AgentTaskGenerationState, evaluation: AgentTaskGenerationEvaluation) -> bool:
        assert runner._champion is not None
        candidate = evaluation.metadata.get("candidate")
        observation = evaluation.metadata.get("observation")
        primary_decision = evaluation.metadata.get("primary_decision")
        decision = evaluation.metadata.get("decision")
        confirmation_observation = evaluation.metadata.get("confirmation_observation")
        confirmation_decision = evaluation.metadata.get("confirmation_decision")
        attempt_id = evaluation.metadata.get("attempt_id")
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
        if not isinstance(attempt_id, str):
            raise TypeError("kernel evaluation metadata is missing its durable attempt identity")
        parent = runner._champion
        record = runner._new_record(
            generation=state.generation + 1,
            role="candidate",
            candidate=candidate,
            observation=observation,
            primary_decision=primary_decision,
            decision=decision,
            parent=parent,
            confirmation_required=runner._confirmation_fn is not None,
            confirmation_observation=confirmation_observation,
            confirmation_decision=confirmation_decision,
            attempt_id=attempt_id,
        )
        runner._persist_record(record)
        if confirmation_identity_unavailable(
            finite_sample=runner._finite_sample,
            decision=confirmation_decision,
            observation=confirmation_observation,
        ):
            raise KernelIntegrityError(
                "confirmation protocol identity is unavailable; the adaptive campaign cannot continue safely"
            )
        if decision.reason in {"harness_modified", "confirmation_harness_modified"}:
            raise KernelIntegrityError(decision.feedback)
        if decision.promote:
            runner._champion = _Champion(candidate, observation, record)
            runner._store.write_champion(candidate, record)
            runner._journal.refresh_artifact_index()
        return decision.promote
    evolution = AgentTaskEvolutionRunner(
        task_prompt=runner.config.task_prompt,
        generate_fn=runner._generate_source,
        evaluate_fn=evaluate_source,
        task_name=f"kernel:{runner.config.problem_id}",
        promotion_fn=promote,
        preserve_generated_output=True,
    )
    try:
        for _ in range(state.generation, proposals):
            if runner._journal.stop_requested():
                raise KernelGenerationCancelled("kernel campaign stop requested before the next proposal")
            state = evolution.run_generation(state)
            runner._journal.refresh_artifact_index()
    except BaseException as exc:
        assert runner._champion is not None
        status = (
            "cancelled"
            if isinstance(exc, KernelGenerationCancelled)
            else "interrupted"
            if isinstance(exc, KeyboardInterrupt)
            else "failed"
        )
        try:
            runner._store.write_manifest(
                runner._manifest(
                    status=status,
                    proposals_requested=proposals,
                    hardware_scope_id=baseline_observation.hardware_scope_id,
                    baseline_id=baseline_observation.baseline_id,
                    protocol_id=baseline_observation.protocol_id,
                    protocol_compatibility_id=baseline_observation.protocol_compatibility_id,
                    champion_attempt_id=runner._champion.record.attempt_id,
                    attempts=len(runner._attempts),
                    error_type=type(exc).__name__,
                    error=terminal_error_text(
                        exc,
                        finite_sample=runner._finite_sample,
                        confirmation_enabled=runner._confirmation_fn is not None,
                        quarantine_primary_evidence=runner._quarantine_primary_evidence,
                    ),
                )
            )
        except Exception:
            pass
        if status == "failed":
            release_sealed_audit_best_effort(runner._store)
        try:
            runner._journal.refresh_artifact_index()
        except Exception:
            pass
        raise

    assert runner._champion is not None
    champion_speedup = runner._champion.observation.speedup_vs_reference
    assert champion_speedup is not None
    policy_id = runner._decision_policy.policy_id if runner._finite_sample else None
    result = KernelEvolutionResult(
        schema_version=("autocontext.kernel-result/v4" if runner._finite_sample else "autocontext.kernel-result/v3"),
        run_id=runner.run_id,
        problem_id=runner.config.problem_id,
        hardware_scope_id=baseline_observation.hardware_scope_id,
        baseline_id=baseline_observation.baseline_id,
        protocol_id=baseline_observation.protocol_id,
        protocol_compatibility_id=baseline_observation.protocol_compatibility_id,
        precision_profile=observed_profile,
        baseline_attempt_id=baseline_record.attempt_id,
        champion_attempt_id=runner._champion.record.attempt_id,
        artifact_identity_version=runner._champion.candidate.artifact_identity_version,
        champion_artifact_digest=runner._champion.candidate.artifact_digest,
        champion_source_digest=runner._champion.candidate.source_digest,
        champion_source=runner._champion.candidate.source,
        champion_score=runner._score(runner._champion.observation),
        champion_speedup_vs_reference=champion_speedup,
        decision_policy=runner._decision_policy,
        **({"decision_policy_id": policy_id} if policy_id is not None else {}),
        attempts=list(runner._attempts),
        playbook=state.playbook,
    )
    runner._store.release_sealed_audit()
    runner._store.write_summary(result)
    runner._store.write_manifest(
        runner._manifest(
            status="complete",
            proposals_requested=proposals,
            hardware_scope_id=result.hardware_scope_id,
            baseline_id=result.baseline_id,
            protocol_id=result.protocol_id,
            protocol_compatibility_id=result.protocol_compatibility_id,
            champion_attempt_id=result.champion_attempt_id,
            attempts=len(result.attempts),
        )
    )
    runner._journal.refresh_artifact_index()
    return result


__all__ = ["run_kernel_evolution"]
