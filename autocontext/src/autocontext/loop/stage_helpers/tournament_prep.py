"""Stage helpers — tournament_prep (extracted from stages.py, AC-482)."""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal

from autocontext.harness.evaluation.failure_report import FailureReport
from autocontext.harness.evaluation.runner import EvaluationRunner
from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from autocontext.harness.evaluation.self_play import (
    SelfPlayConfig,
    build_opponent_pool,
    load_self_play_pool,
)
from autocontext.harness.evaluation.types import EvaluationLimits as HarnessLimits
from autocontext.harness.evaluation.types import EvaluationSummary
from autocontext.harness.pipeline.holdout import HoldoutPolicy, HoldoutResult, HoldoutVerifier
from autocontext.loop.stage_helpers.persistence_helpers import _revise_strategy_for_validity_failure
from autocontext.loop.stage_types import GenerationContext
from autocontext.loop.tournament_helpers import build_retry_prompt, build_validity_rollback
from autocontext.scenarios.families import detect_family

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.config.settings import AppSettings
    from autocontext.execution.supervisor import ExecutionSupervisor
    from autocontext.harness.evaluation.types import EvaluationResult
    from autocontext.harness.pipeline.validity_gate import ValidityGate
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.scenarios.base import ScenarioInterface
    from autocontext.storage import SQLiteStore


def _build_empty_tournament(ctx: GenerationContext) -> EvaluationSummary:
    """Create a zero-match summary for rollback paths that skip execution."""
    return EvaluationSummary(
        mean_score=0.0,
        best_score=0.0,
        wins=0,
        losses=0,
        elo_after=ctx.challenger_elo,
        results=[],
        scoring_backend=ctx.settings.scoring_backend,
        uncertainty_after=ctx.challenger_uncertainty,
    )


def _build_live_opponent_pool(
    ctx: GenerationContext,
    *,
    sqlite: SQLiteStore,
) -> tuple[Any, list[dict[str, Any]], int]:
    """Build the same opponent schedule used by the live tournament path."""
    settings = ctx.settings
    self_play_config = SelfPlayConfig(
        enabled=settings.self_play_enabled,
        pool_size=settings.self_play_pool_size,
        weight=settings.self_play_weight,
    )
    self_play_pool = load_self_play_pool(
        sqlite.get_self_play_strategy_history(ctx.run_id) if settings.self_play_enabled else [],
        self_play_config,
        current_generation=ctx.generation,
    )
    opponent_pool = build_opponent_pool(
        [{"source": "baseline"}],
        self_play_pool,
        trials=settings.matches_per_generation,
    )
    planned_self_play_matches = sum(
        1 for entry in opponent_pool if isinstance(entry, dict) and entry.get("source") == "self_play"
    )
    return self_play_pool, opponent_pool, planned_self_play_matches


def _build_skeptic_review_section(ctx: GenerationContext) -> str:
    """Render skeptic findings into curator-readable context."""
    review = ctx.skeptic_review
    if review is None:
        return ""
    concerns = review.concerns or ["No concrete concerns captured."]
    concerns_block = "\n".join(f"- {concern}" for concern in concerns)
    return (
        "SKEPTIC REVIEW:\n"
        f"Risk level: {review.risk_level}\n"
        f"Recommendation: {review.recommendation}\n"
        f"Confidence: {review.confidence}/10\n"
        "Concerns:\n"
        f"{concerns_block}\n"
    )


def _resolve_holdout_policy(ctx: GenerationContext) -> HoldoutPolicy:
    """Build the effective holdout policy, including scenario-family overrides."""
    family = detect_family(ctx.scenario)
    family_marker = family.scenario_type_marker if family is not None else ""
    policy = HoldoutPolicy(
        holdout_seeds=ctx.settings.holdout_seeds,
        min_holdout_score=ctx.settings.holdout_min_score,
        max_generalization_gap=ctx.settings.holdout_max_regression_gap,
        seed_offset=ctx.settings.holdout_seed_offset,
        enabled=ctx.settings.holdout_enabled,
        metadata={"family": family_marker} if family_marker else {},
    )
    if family is None:
        return policy

    override = ctx.settings.holdout_family_policies.get(family.scenario_type_marker) or ctx.settings.holdout_family_policies.get(
        family.name
    )
    if not isinstance(override, dict):
        return policy

    merged = policy.to_dict()
    merged.update(override)
    metadata = dict(policy.metadata)
    override_metadata = override.get("metadata")
    if isinstance(override_metadata, dict):
        metadata.update(override_metadata)
    if family_marker:
        metadata.setdefault("family", family_marker)
    merged["metadata"] = metadata
    return HoldoutPolicy.from_dict(merged)


def _run_holdout_verification(
    ctx: GenerationContext,
    *,
    supervisor: ExecutionSupervisor,
    strategy: dict[str, Any],
    in_sample_score: float,
    limits: HarnessLimits,
) -> HoldoutResult | None:
    """Verify an advancing candidate on holdout seeds when enabled."""
    policy = _resolve_holdout_policy(ctx)
    if not policy.enabled:
        return None

    evaluator = ScenarioEvaluator(ctx.scenario, supervisor, hook_bus=ctx.hook_bus)

    def _evaluate(candidate: dict[str, Any], seed: int) -> float:
        return evaluator.evaluate(candidate, seed, limits).score

    verifier = HoldoutVerifier(policy=policy, evaluate_fn=_evaluate)
    result = verifier.verify(strategy=strategy, in_sample_score=in_sample_score)
    metadata = dict(result.metadata)
    metadata["policy"] = policy.to_dict()
    result.metadata = metadata
    return result


@dataclasses.dataclass(slots=True)
class _ValidityGateOutcome:
    """Result of one validity-gate check inside stage_tournament's retry loop."""

    action: Literal["proceed", "retry", "return"]
    current_strategy: dict[str, Any]
    validity_retry_attempt: int


def _run_validity_gate(
    ctx: GenerationContext,
    *,
    validity_gate: ValidityGate,
    current_strategy: dict[str, Any],
    validity_retry_attempt: int,
    agents: AgentOrchestrator | None,
    events: EventStreamEmitter,
    settings: AppSettings,
) -> _ValidityGateOutcome:
    """Tier 1 validity gate (AC-160): pass through, revise-and-retry, or roll back
    without spending a tournament once the retry budget is exhausted."""
    validity_result = validity_gate.check(current_strategy)
    if validity_result.passed:
        events.emit(
            "validity_check_passed",
            {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
            },
        )
        return _ValidityGateOutcome("proceed", current_strategy, validity_retry_attempt)

    events.emit(
        "validity_check_failed",
        {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "errors": validity_result.errors,
            "retry_budget_remaining": validity_result.retry_budget_remaining,
        },
    )
    can_retry = validity_gate.consume_retry()
    if can_retry:
        validity_retry_attempt += 1
        revised_strategy = _revise_strategy_for_validity_failure(
            ctx,
            current_strategy=current_strategy,
            errors=validity_result.errors,
            retry_attempt=validity_retry_attempt,
            agents=agents,
        )
        if revised_strategy is not None:
            current_strategy = revised_strategy
        time.sleep(settings.retry_backoff_seconds * validity_retry_attempt)
        return _ValidityGateOutcome("retry", current_strategy, validity_retry_attempt)

    # Validity budget exhausted: rollback without tournament
    tournament = _build_empty_tournament(ctx)
    rollback = build_validity_rollback(
        current_strategy=current_strategy,
        validity_retry_attempts=validity_retry_attempt,
        score_history=ctx.score_history,
        gate_decision_history=ctx.gate_decision_history,
        tournament=tournament,
    )
    gate_decision = rollback["gate_decision"]
    gate_delta = rollback["gate_delta"]
    events.emit(
        "gate_decided",
        {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "decision": gate_decision,
            "delta": gate_delta,
            "tier": "validity",
        },
    )
    ctx.score_history[:] = rollback["score_history"]
    ctx.gate_decision_history[:] = rollback["gate_decision_history"]
    ctx.gate_decision = gate_decision
    ctx.gate_delta = gate_delta
    ctx.current_strategy = rollback["current_strategy"]
    ctx.attempt = rollback["attempt"]
    ctx.tournament = rollback["tournament"]
    return _ValidityGateOutcome("return", current_strategy, validity_retry_attempt)


@dataclasses.dataclass(slots=True)
class _TournamentAttemptOutcome:
    """Result of one tournament-execution attempt inside stage_tournament's retry loop."""

    should_retry: bool
    tournament: EvaluationSummary | None
    attempt: int
    harness_limits: HarnessLimits | None


def _execute_tournament_with_retry(
    ctx: GenerationContext,
    *,
    supervisor: ExecutionSupervisor,
    scenario: ScenarioInterface,
    sqlite: SQLiteStore,
    events: EventStreamEmitter,
    settings: AppSettings,
    current_strategy: dict[str, Any],
    attempt: int,
) -> _TournamentAttemptOutcome:
    """Run one round of tournament matches, retrying on transient exceptions."""
    self_play_pool, opponent_pool, planned_self_play_matches = _build_live_opponent_pool(
        ctx,
        sqlite=sqlite,
    )

    events.emit(
        "tournament_started",
        {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "matches": settings.matches_per_generation,
            "scoring_backend": settings.scoring_backend,
            "self_play_pool_size": self_play_pool.size,
            "self_play_matches_planned": planned_self_play_matches,
        },
    )

    def _on_match(match_index: int, score: float, _gen: int = ctx.generation) -> None:
        events.emit(
            "match_completed",
            {
                "run_id": ctx.run_id,
                "generation": _gen,
                "match_index": match_index,
                "score": score,
            },
        )

    try:
        evaluator = ScenarioEvaluator(scenario, supervisor, hook_bus=ctx.hook_bus)
        harness_limits = HarnessLimits()

        def _on_result(idx: int, result: EvaluationResult) -> None:
            _on_match(idx, result.score)

        runner = EvaluationRunner(evaluator, scoring_backend=settings.scoring_backend)
        tournament = runner.run(
            candidate=current_strategy,
            seed_base=settings.seed_base + (ctx.generation * 100) + (attempt * 10),
            trials=settings.matches_per_generation,
            limits=harness_limits,
            challenger_elo=ctx.challenger_elo,
            challenger_uncertainty=ctx.challenger_uncertainty,
            opponent_pool=opponent_pool,
            on_result=_on_result,
        )
    except Exception:
        logger.debug("loop.stages: caught Exception", exc_info=True)
        attempt += 1
        if attempt > settings.max_retries:
            raise
        time.sleep(settings.retry_backoff_seconds * attempt)
        return _TournamentAttemptOutcome(should_retry=True, tournament=None, attempt=attempt, harness_limits=None)

    return _TournamentAttemptOutcome(
        should_retry=False,
        tournament=tournament,
        attempt=attempt,
        harness_limits=harness_limits,
    )


def _run_retry_learning(
    ctx: GenerationContext,
    *,
    agents: AgentOrchestrator | None,
    scenario: ScenarioInterface,
    sqlite: SQLiteStore,
    settings: AppSettings,
    tournament: EvaluationSummary,
    current_strategy: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    """Re-invoke the competitor with failure context after a retry gate decision."""
    if agents is None or ctx.prompts is None:
        return current_strategy

    is_code_strategy = "__code__" in current_strategy
    failure_report_context = FailureReport.from_tournament(
        tournament,
        previous_best=ctx.previous_best,
        threshold=settings.backpressure_min_delta,
        strategy=current_strategy,
    ).to_prompt_context()
    retry_prompt = build_retry_prompt(
        base_prompt=ctx.prompts.competitor,
        tournament_best_score=tournament.best_score,
        previous_best=ctx.previous_best,
        min_delta=settings.backpressure_min_delta,
        current_strategy=current_strategy,
        attempt=attempt,
        is_code_strategy=is_code_strategy,
        include_code_strategy_suffix=settings.code_strategies_enabled,
        strategy_interface=ctx.strategy_interface,
        failure_report_context=failure_report_context,
    )
    try:
        raw_text, _ = agents.competitor.run(retry_prompt, tool_context=ctx.tool_context)
        if is_code_strategy:
            revised_strategy, _ = agents.translator.translate_code(raw_text)
        else:
            revised_strategy, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
        if "__code__" not in revised_strategy:
            state = scenario.initial_state(seed=settings.seed_base + ctx.generation)
            valid, reason = scenario.validate_actions(state, "challenger", revised_strategy)
            if valid:
                current_strategy = revised_strategy
                sqlite.append_agent_output(
                    ctx.run_id,
                    ctx.generation,
                    "competitor",
                    json.dumps(revised_strategy, sort_keys=True),
                )
        else:
            current_strategy = revised_strategy
            sqlite.append_agent_output(
                ctx.run_id,
                ctx.generation,
                "competitor",
                json.dumps(revised_strategy, sort_keys=True),
            )
    except Exception:
        logger.debug("retry-learning competitor re-invocation failed", exc_info=True)
    return current_strategy
