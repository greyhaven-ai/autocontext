"""Stage helpers — tournament_prep (extracted from stages.py, AC-482)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from autocontext.harness.evaluation.self_play import (
    SelfPlayConfig,
    build_opponent_pool,
    load_self_play_pool,
)
from autocontext.harness.evaluation.types import EvaluationLimits as HarnessLimits
from autocontext.harness.evaluation.types import EvaluationSummary
from autocontext.harness.pipeline.holdout import HoldoutPolicy, HoldoutResult, HoldoutVerifier
from autocontext.loop.stage_types import GenerationContext
from autocontext.scenarios.families import detect_family

if TYPE_CHECKING:
    from autocontext.execution.supervisor import ExecutionSupervisor
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
        1
        for entry in opponent_pool
        if isinstance(entry, dict) and entry.get("source") == "self_play"
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

    override = (
        ctx.settings.holdout_family_policies.get(family.scenario_type_marker)
        or ctx.settings.holdout_family_policies.get(family.name)
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

    evaluator = ScenarioEvaluator(ctx.scenario, supervisor)

    def _evaluate(candidate: dict[str, Any], seed: int) -> float:
        return evaluator.evaluate(candidate, seed, limits).score

    verifier = HoldoutVerifier(policy=policy, evaluate_fn=_evaluate)
    result = verifier.verify(strategy=strategy, in_sample_score=in_sample_score)
    metadata = dict(result.metadata)
    metadata["policy"] = policy.to_dict()
    result.metadata = metadata
    return result
