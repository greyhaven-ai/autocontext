"""Replay a durable kernel lineage into the exact next adaptive proposal state."""

from __future__ import annotations

from typing import Any

from autocontext.execution.agent_task_evolution import (
    AgentTaskGenerationState,
    LessonSignal,
    accumulate_lessons,
)
from autocontext.kernel_evolution._generation_replay import (
    revalidated_generation_record,
    validate_generation_replay,
)
from autocontext.kernel_evolution.adaptive_evidence import confirmation_identity_unavailable
from autocontext.kernel_evolution.generation import (
    KernelGenerationFailure,
    KernelGenerationResult,
)
from autocontext.kernel_evolution.models import (
    KernelAttemptRecord,
    KernelCandidate,
    KernelPromotionDecision,
    content_digest,
)
from autocontext.scenarios.agent_task import AgentTaskResult


def restore_kernel_run(
    runner: Any,
    *,
    proposals: int,
) -> tuple[KernelCandidate, KernelAttemptRecord, AgentTaskGenerationState, str | None] | None:
    """Validate persisted identities and reconstruct the champion/playbook exactly."""
    if not runner._resume:
        return None
    manifest = runner._store.read_manifest()
    _validate_manifest(runner, manifest, proposals=proposals)
    _reload_generation_activity(runner)
    runner._store.reconcile_attempt_files(
        expected_attempt_ids=runner._journal.evaluation_claim_attempt_ids()
    )
    attempts = runner._store.read_attempts()
    runner._journal.assert_resumable(
        attempts_by_id={item.attempt_id for item in attempts},
        expected_generation_identity=runner._generator_identity,
        claim_resume_safe=bool(getattr(runner._generate_fn, "supports_claim_resume", False)),
        call_fence_resume_safe=runner._call_fence_resume_safe,
    )
    _restore_generation_history(runner)
    _reject_unavailable_confirmation(runner, manifest, attempts)
    summary = runner._store.read_summary()
    if manifest.get("status") == "complete":
        if summary is None:
            raise ValueError("complete kernel campaign is missing its summary")
        if attempts != summary.attempts:
            raise ValueError("complete kernel summary and append-only lineage disagree")
        for attempt in attempts:
            if attempt.role == "candidate":
                generation = runner._journal.read_generation_result(attempt.generation)
                if generation is None:
                    raise ValueError(f"candidate attempt {attempt.attempt_id} has no generation receipt")
                runner._journal.link_attempt(
                    generation,
                    attempt_id=attempt.attempt_id,
                    artifact_digest=attempt.artifact_digest,
                )
        runner._attempts = list(attempts)
        champion_record = next(item for item in attempts if item.attempt_id == summary.champion_attempt_id)
        champion = runner._store.read_candidate(champion_record)
        runner._store.validate_champion(champion, champion_record)
        from autocontext.kernel_evolution.runner import _Champion

        runner._champion = _Champion(champion, champion_record.observation, champion_record)
        return champion, attempts[0], _state_from_attempts(runner, attempts), summary.precision_profile

    runner._journal.clear_stop_for_resume()
    if not attempts:
        return None
    baseline = attempts[0]
    if baseline.role != "baseline" or baseline.decision != "baseline":
        raise ValueError("resumable kernel lineage does not contain a valid baseline root")
    if len(attempts) - 1 > proposals:
        raise ValueError("persisted kernel lineage exceeds the requested proposal target")
    runner._attempts = list(attempts)
    champion_record = baseline
    for attempt in attempts[1:]:
        generation = runner._journal.read_generation_result(attempt.generation)
        if generation is None:
            raise ValueError(f"candidate attempt {attempt.attempt_id} has no generation receipt")
        runner._generation_results[attempt.generation] = generation
        runner._journal.link_attempt(
            generation,
            attempt_id=attempt.attempt_id,
            artifact_digest=attempt.artifact_digest,
        )
        if attempt.decision == "promoted":
            champion_record = attempt
        confirmation = attempt.confirmation_observation
        if confirmation is not None and confirmation.report is not None and confirmation.protocol_id is not None:
            runner._used_confirmation_evidence_ids.add(f"protocol:{confirmation.protocol_id}")
            runner._used_confirmation_evidence_ids.add(f"plan:{confirmation.report.protocol.seed_commitment}")
    champion = runner._store.read_candidate(champion_record)
    from autocontext.kernel_evolution.runner import _Champion

    runner._champion = _Champion(champion, champion_record.observation, champion_record)
    runner._store.write_champion(champion, champion_record)
    baseline_report = baseline.observation.report
    observed_profile = (
        baseline_report.protocol.semantics.profile_name
        if baseline_report is not None and baseline_report.protocol.semantics is not None
        else None
    )
    return champion, baseline, _state_from_attempts(runner, attempts), observed_profile


def adaptive_feedback_for_attempt(
    runner: Any,
    attempt: KernelAttemptRecord,
) -> tuple[str, float, dict[str, float], LessonSignal]:
    primary = attempt.primary_decision
    if primary is None:
        raise ValueError("verified kernel attempt is missing its primary decision")
    observation = attempt.observation
    aggregate = runner._evaluator.config.adaptive_feedback_policy == "aggregate-gates"
    if aggregate:
        gate_status = {gate.name: gate.status for gate in primary.gates}
        feedback = (
            "Aggregate benchmark gates: "
            + ", ".join(f"{gate.name}={gate.status}" for gate in primary.gates)
            + f". Disposition={primary.reason}."
        )
        score = float(primary.promote)
        performance = float(gate_status.get("relative_improvement") == "passed")
        metrics: dict[str, float] = {}
    else:
        feedback = primary.feedback
        score = runner._score(observation)
        performance = min(1.0, float(observation.speedup_vs_incumbent or 0.0))
        metrics = {}
        if observation.relative_improvement is not None:
            metrics["relative_improvement"] = float(observation.relative_improvement)
        if observation.speedup_lcb is not None:
            metrics["speedup_lcb"] = float(observation.speedup_lcb)
    dimensions = {
        "correctness": 1.0 if observation.eligible else 0.0,
        "performance": performance,
        "promotion_gate": 1.0 if primary.promote else 0.0,
    }
    signal = LessonSignal(
        hint=feedback,
        plateau=primary.reason in {"insufficient_improvement", "confidence_interval"},
        metrics=metrics,
    )
    return feedback, score, dimensions, signal


def _restore_generation_history(runner: Any) -> None:
    restore_generation = getattr(runner._generate_fn, "restore", None)
    if callable(restore_generation):
        restore_generation(
            runner._generation_results[index]
            for index in sorted(runner._generation_results)
        )
    proposal_index = len(runner._generation_results) + 1
    if runner._journal.read_generation_claim(proposal_index) is None:
        return
    failures, unresolved = runner._journal.generation_call_state(proposal_index)
    if unresolved:
        return
    restore_completed = getattr(runner._generate_fn, "restore_completed_pending_failures", None)
    restore_pending = getattr(runner._generate_fn, "restore_pending_failures", None)
    if callable(restore_completed) and failures:
        restore_completed(proposal_index, failures)
    elif callable(restore_pending) and failures:
        restore_pending(proposal_index, failures)


def _reload_generation_activity(runner: Any) -> None:
    results = runner._journal.generation_results(recover_orphans=True)
    runner._generation_results = {item.proposal_index: item for item in results}
    activity_results, incomplete_failures = runner._journal.generation_activity()
    if activity_results != results:
        raise ValueError("generation activity changed while restoring the execution lease")
    validate_generation_budget_contract(runner, results, incomplete_failures)
    runner._require_generation_budget()


def validate_generation_budget_contract(
    runner: Any,
    results: tuple[KernelGenerationResult, ...] | list[KernelGenerationResult],
    incomplete_failures: tuple[KernelGenerationFailure, ...] = (),
) -> None:
    """Validate per-proposal and per-call ceilings, not only aggregate totals."""
    checked_results = tuple(revalidated_generation_record(item) for item in results)
    checked_failures = tuple(revalidated_generation_record(item) for item in incomplete_failures)
    system_prompt = getattr(runner._generate_fn, "system_prompt", None)
    validate_generation_replay(
        checked_results,
        checked_failures,
        runner._generation_budget,
        expected_provider=getattr(runner._generate_fn, "provider_id", None),
        expected_system_prompt_digest=(
            content_digest(system_prompt.encode("utf-8")) if isinstance(system_prompt, str) else None
        ),
        expected_source_suffix=runner.config.source_suffix,
        expected_entrypoint=runner.config.entrypoint,
        enforce_aggregate_budget=False,
    )


def _reject_unavailable_confirmation(
    runner: Any,
    manifest: dict[str, Any],
    attempts: list[KernelAttemptRecord],
) -> None:
    if not any(
        confirmation_identity_unavailable(
            finite_sample=runner._finite_sample,
            decision=attempt.confirmation_decision,
            observation=attempt.confirmation_observation,
        )
        for attempt in attempts
    ):
        return
    failed = dict(manifest)
    failed.update(
        status="failed",
        error_type="KernelIntegrityError",
        error="confirmation protocol identity is unavailable; campaign cannot resume safely",
    )
    runner._store.write_manifest(failed)
    runner._journal.refresh_artifact_index()
    raise ValueError("confirmation protocol identity is unavailable; campaign cannot resume safely")


def _state_from_attempts(runner: Any, attempts: list[KernelAttemptRecord]) -> AgentTaskGenerationState:
    baseline = attempts[0]
    baseline_candidate = runner._store.read_candidate(baseline)
    baseline_score = runner._score(baseline.observation)
    best_output = baseline_candidate.source
    best_score = baseline_score
    playbook = ""
    score_history = [baseline_score]
    lesson_history: list[str] = []
    for attempt in attempts[1:]:
        candidate = runner._store.read_candidate(attempt)
        feedback, score, dimensions, signal = adaptive_feedback_for_attempt(runner, attempt)
        lesson = accumulate_lessons(
            AgentTaskResult(score=score, reasoning=feedback, dimension_scores=dimensions),
            attempt.generation,
            signal=signal,
        )
        if lesson:
            playbook = (playbook + "\n" + lesson).strip() if playbook else lesson
        score_history.append(score)
        lesson_history.append(lesson)
        decision: KernelPromotionDecision | None = attempt.promotion_decision
        if decision is not None and decision.promote:
            best_output = candidate.source
            best_score = score
    return AgentTaskGenerationState(
        generation=len(attempts) - 1,
        best_output=best_output,
        best_score=best_score,
        playbook=playbook,
        score_history=score_history,
        lesson_history=lesson_history,
        metadata={},
    )


def _validate_manifest(runner: Any, manifest: dict[str, Any], *, proposals: int) -> None:
    baseline = KernelCandidate(
        source=runner.config.baseline_source,
        source_suffix=runner.config.source_suffix,
        entrypoint=runner.config.entrypoint,
    )
    expected = {
        "run_id": runner.run_id,
        "problem_id": runner.config.problem_id,
        "baseline_artifact_digest": baseline.artifact_digest,
        "baseline_source_digest": baseline.source_digest,
        "evolution": runner._evolution_contract(),
        "decision_policy_id": runner._decision_policy.policy_id,
        "benchmark": runner._evaluator.manifest(),
        "confirmation": runner._confirmation_contract(),
    }
    for name, value in expected.items():
        if manifest.get(name) != value:
            raise ValueError(f"resumed kernel campaign {name} conflicts with the active plan")
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        raise ValueError("resumed kernel campaign has no generation contract")
    if generation.get("budget_id") != runner._generation_budget.budget_id:
        raise ValueError("resumed kernel campaign generation budget changed")
    if generation.get("budget") != runner._generation_budget.model_dump(mode="json"):
        raise ValueError("resumed kernel campaign generation budget payload changed")
    if generation.get("claim_resume_safe") != bool(
        getattr(runner._generate_fn, "supports_claim_resume", False)
    ):
        raise ValueError("resumed kernel campaign generation resume capability changed")
    if generation.get("call_fence_resume_safe") != runner._call_fence_resume_safe:
        raise ValueError("resumed kernel campaign call-fence resume capability changed")
    if generation.get("generator_identity") != runner._generator_identity:
        raise ValueError("resumed kernel campaign generator identity changed")
    target = manifest.get("proposals_requested")
    if target is not None and target != proposals:
        raise ValueError("resumed kernel campaign proposal target changed")
    if manifest.get("status") in {"baseline_failed", "failed"}:
        raise ValueError("a terminally failed kernel campaign cannot be resumed")


__all__ = ["adaptive_feedback_for_attempt", "restore_kernel_run"]
