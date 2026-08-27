"""Matched, adaptive confirmation for context-bundle candidates (AC-973)."""

from __future__ import annotations

import math
import statistics

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.context_bundles.models import (
    ComparisonDecision,
    ComparisonResult,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    TrialLane,
)


def evaluate_matched_trials(
    candidate: ContextBundle,
    trials: list[MatchedTrial] | tuple[MatchedTrial, ...],
    *,
    policy: ConfirmationPolicy | None = None,
) -> ComparisonResult:
    """Evaluate one candidate without ever comparing unmatched observations.

    Each input row contains both arms for the same fixture, seed, cohort and
    evaluator epoch. Duplicate pair keys and lineage drift fail closed.
    """
    effective_policy = policy or ConfirmationPolicy()
    _validate_trials(candidate, trials)
    by_lane = {lane: [trial for trial in trials if trial.lane == lane] for lane in TrialLane}
    screen = by_lane[TrialLane.SCREEN]
    confirmation = by_lane[TrialLane.CONFIRMATION]
    heldout = by_lane[TrialLane.HELDOUT]

    counts = {
        "screen_pairs": len(screen),
        "confirmation_pairs": len(confirmation),
        "heldout_pairs": len(heldout),
    }
    if len(confirmation) > effective_policy.max_confirmation_pairs:
        raise ValueError("confirmation pairs exceed the configured maximum")
    if len(screen) < effective_policy.min_screen_pairs:
        return ComparisonResult(
            decision=ComparisonDecision.NEEDS_SCREEN,
            reason="insufficient matched screen pairs",
            **counts,
        )
    if any(not trial.candidate_valid or not trial.incumbent_valid for trial in screen):
        return ComparisonResult(
            decision=ComparisonDecision.REJECTED,
            reason="screen validity failure",
            **counts,
        )
    screen_effect = statistics.fmean(trial.delta for trial in screen)
    if screen_effect <= effective_policy.min_effect:
        return ComparisonResult(
            decision=ComparisonDecision.REJECTED,
            reason="candidate failed the cheap matched screen",
            mean_effect=screen_effect,
            **counts,
        )
    if len(confirmation) < effective_policy.min_confirmation_pairs:
        return ComparisonResult(
            decision=ComparisonDecision.NEEDS_CONFIRMATION,
            reason="screen passed; more matched confirmation pairs required",
            mean_effect=screen_effect,
            **counts,
        )
    if any(not trial.candidate_valid or not trial.incumbent_valid for trial in confirmation):
        return ComparisonResult(
            decision=ComparisonDecision.REJECTED,
            reason="confirmation validity failure",
            **counts,
        )

    deltas = [trial.delta for trial in confirmation]
    max_looks = effective_policy.max_confirmation_pairs - effective_policy.min_confirmation_pairs + 1
    mean_effect, low, high = paired_confidence_interval(
        deltas,
        effective_policy.confidence_z,
        max_looks=max_looks,
    )
    assert mean_effect is not None and low is not None and high is not None
    if high <= effective_policy.min_effect:
        return ComparisonResult(
            decision=ComparisonDecision.REJECTED,
            reason="confirmation confidence interval is below the minimum effect",
            mean_effect=mean_effect,
            confidence_low=low,
            confidence_high=high,
            **counts,
        )
    if low <= effective_policy.min_effect:
        decision = (
            ComparisonDecision.INCONCLUSIVE
            if len(confirmation) >= effective_policy.max_confirmation_pairs
            else ComparisonDecision.NEEDS_CONFIRMATION
        )
        return ComparisonResult(
            decision=decision,
            reason=(
                "maximum confirmation budget reached without a decisive effect"
                if decision == ComparisonDecision.INCONCLUSIVE
                else "confirmation uncertainty overlaps the minimum effect"
            ),
            mean_effect=mean_effect,
            confidence_low=low,
            confidence_high=high,
            **counts,
        )
    if len(heldout) < effective_policy.min_heldout_pairs:
        return ComparisonResult(
            decision=ComparisonDecision.NEEDS_HELDOUT,
            reason="confirmation passed; held-out matched pairs required",
            mean_effect=mean_effect,
            confidence_low=low,
            confidence_high=high,
            **counts,
        )
    if any(not trial.candidate_valid or not trial.incumbent_valid for trial in heldout):
        return ComparisonResult(
            decision=ComparisonDecision.REJECTED,
            reason="held-out validity failure",
            mean_effect=mean_effect,
            confidence_low=low,
            confidence_high=high,
            **counts,
        )
    heldout_effect = statistics.fmean(trial.delta for trial in heldout)
    if heldout_effect <= effective_policy.min_effect:
        return ComparisonResult(
            decision=ComparisonDecision.REJECTED,
            reason="candidate regressed on the held-out lane",
            mean_effect=heldout_effect,
            confidence_low=low,
            confidence_high=high,
            **counts,
        )
    return ComparisonResult(
        decision=ComparisonDecision.CONFIRMED,
        reason="matched confirmation and held-out lanes passed",
        mean_effect=mean_effect,
        confidence_low=low,
        confidence_high=high,
        **counts,
    )


def _validate_trials(candidate: ContextBundle, trials: list[MatchedTrial] | tuple[MatchedTrial, ...]) -> None:
    seen: set[str] = set()
    for trial in trials:
        if trial.candidate_digest != candidate.digest:
            raise ValueError("trial candidate digest does not match the candidate bundle")
        if trial.incumbent_digest != candidate.parent_digest:
            raise ValueError("trial incumbent digest does not match the candidate parent")
        if trial.evaluator_epoch != candidate.evaluator_epoch:
            raise ValueError("trial evaluator epoch does not match the candidate bundle")
        if not trial.cohort.strip() or not trial.fixture.strip() or not trial.fixture_digest.strip():
            raise ValueError("matched trials require cohort, fixture, and fixture_digest")
        if isinstance(trial.seed, bool) or not isinstance(trial.seed, int):
            raise ValueError("matched trial seed must be an integer")
        if not all(math.isfinite(value) for value in (trial.candidate_score, trial.incumbent_score, trial.delta)):
            raise ValueError("matched trial scores and effect must be finite")
        if trial.pair_key in seen:
            raise ValueError("duplicate matched trial pair")
        seen.add(trial.pair_key)
