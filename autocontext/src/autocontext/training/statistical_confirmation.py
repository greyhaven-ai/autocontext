"""Noise-aware, matched-trial checkpoint confirmation (AC-976)."""

from __future__ import annotations

import math
from collections.abc import Callable
from statistics import fmean
from typing import Any, Literal

from pydantic import Field

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.context_bundles.models import stable_digest
from autocontext.util.models import StrictModel

PromotionDecision = Literal[
    "accepted",
    "rejected",
    "inconclusive",
    "invalid",
    "infrastructure_error",
    "needs_more_trials",
]
TrialLane = Literal["screen", "confirmation", "heldout"]
ProtocolMode = Literal["adaptive", "deterministic"]


class TrainingPromotionProtocol(StrictModel):
    mode: ProtocolMode = "adaptive"
    initial_screen_pairs: int = Field(default=2, ge=1)
    min_confirmation_pairs: int = Field(default=4, ge=2)
    max_confirmation_pairs: int = Field(default=12, ge=2)
    confirmation_batch_size: int = Field(default=2, ge=1)
    heldout_pairs: int = Field(default=0, ge=0)
    min_effect: float = 0.01
    confidence_z: float = Field(default=1.96, gt=0)
    dimension_regression_tolerance: float = Field(default=0.0, ge=0.0)

    def model_post_init(self, __context: Any) -> None:
        del __context
        if self.max_confirmation_pairs < self.min_confirmation_pairs:
            raise ValueError("max_confirmation_pairs must be >= min_confirmation_pairs")
        if not math.isfinite(self.min_effect):
            raise ValueError("min_effect must be finite")
        if not math.isfinite(self.confidence_z):
            raise ValueError("confidence_z must be finite")
        if not math.isfinite(self.dimension_regression_tolerance):
            raise ValueError("dimension_regression_tolerance must be finite")


class TrainingPromotionTrial(StrictModel):
    trial_id: str = Field(min_length=1)
    incumbent_id: str = Field(min_length=1)
    challenger_id: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    evaluator_epoch: str = Field(min_length=1)
    verifier_digest: str = Field(min_length=1)
    cohort: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
    fixture_digest: str = Field(min_length=1)
    seed: int
    lane: TrialLane
    incumbent_score: float
    challenger_score: float
    incumbent_valid: bool = True
    challenger_valid: bool = True
    incumbent_parse_ok: bool = True
    challenger_parse_ok: bool = True
    incumbent_dimensions: dict[str, float] = Field(default_factory=dict)
    challenger_dimensions: dict[str, float] = Field(default_factory=dict)
    evaluation_cost: float = Field(default=0.0, ge=0.0)
    infrastructure_error: str | None = None

    @property
    def delta(self) -> float:
        return round(self.challenger_score - self.incumbent_score, 12)

    @property
    def pair_key(self) -> str:
        # A fixture/seed observation is one experimental unit.  Re-labelling the
        # same unit as screen, confirmation, or heldout must not turn it into
        # independent evidence.
        return stable_digest(
            {
                "scenario": self.scenario,
                "evaluator_epoch": self.evaluator_epoch,
                "verifier_digest": self.verifier_digest,
                "cohort": self.cohort,
                # ``fixture`` is a display/routing label and may have aliases.
                # The digest is the canonical fixture identity.
                "fixture_digest": self.fixture_digest,
                "seed": self.seed,
            }
        )


class TrainingPromotionArtifact(StrictModel):
    schema_version: int = 1
    incumbent_id: str = Field(min_length=1)
    challenger_id: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    evaluator_epoch: str = Field(min_length=1)
    verifier_digest: str = Field(min_length=1)
    cohort: str = Field(min_length=1)
    decision: PromotionDecision
    phase: Literal["screen", "confirmation", "heldout", "complete"]
    reason: str = Field(min_length=1)
    trials: list[TrainingPromotionTrial]
    mean_effect: float | None
    confidence_low: float | None
    confidence_high: float | None
    evaluation_cost: float = Field(ge=0.0)
    next_trial_count: int = Field(ge=0)
    protocol: TrainingPromotionProtocol


TrialExecutor = Callable[[TrialLane, int, str], TrainingPromotionTrial]


def evaluate_training_promotion(
    trials: list[TrainingPromotionTrial],
    *,
    incumbent_id: str,
    challenger_id: str,
    scenario: str,
    evaluator_epoch: str,
    verifier_digest: str,
    cohort: str,
    protocol: TrainingPromotionProtocol | None = None,
) -> TrainingPromotionArtifact:
    """Reproduce a checkpoint decision from raw matched trials."""

    active = protocol or TrainingPromotionProtocol()
    invalid_reason = _validate_trial_set(
        trials,
        incumbent_id=incumbent_id,
        challenger_id=challenger_id,
        scenario=scenario,
        evaluator_epoch=evaluator_epoch,
        verifier_digest=verifier_digest,
        cohort=cohort,
        dimension_tolerance=active.dimension_regression_tolerance,
    )
    cost = round(sum(trial.evaluation_cost for trial in trials if math.isfinite(trial.evaluation_cost)), 6)
    if invalid_reason:
        decision: PromotionDecision = "infrastructure_error" if invalid_reason.startswith("infrastructure error:") else "invalid"
        return _artifact(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            decision,
            "complete",
            invalid_reason,
            trials,
            None,
            None,
            None,
            cost,
            0,
            active,
        )

    screen = [trial for trial in trials if trial.lane == "screen"]
    confirmation = [trial for trial in trials if trial.lane == "confirmation"]
    heldout = [trial for trial in trials if trial.lane == "heldout"]
    if len(confirmation) > active.max_confirmation_pairs:
        return _artifact(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "invalid",
            "complete",
            "confirmation evidence exceeds the configured maximum pair count",
            trials,
            None,
            None,
            None,
            cost,
            0,
            active,
        )
    if len(heldout) > active.heldout_pairs:
        return _artifact(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "invalid",
            "complete",
            "held-out evidence exceeds the configured pair count",
            trials,
            None,
            None,
            None,
            cost,
            0,
            active,
        )
    screen_effects = [trial.delta for trial in screen]
    screen_mean, screen_low, screen_high = _effect_interval(screen_effects, active)

    if active.mode == "deterministic":
        if not screen_effects:
            return _needs_more(
                incumbent_id,
                challenger_id,
                scenario,
                evaluator_epoch,
                verifier_digest,
                cohort,
                "screen",
                "deterministic comparison requires one matched trial",
                trials,
                cost,
                1,
                active,
            )
        accepted = screen_mean is not None and screen_mean >= active.min_effect
        if accepted and len(heldout) < active.heldout_pairs:
            return _needs_more(
                incumbent_id,
                challenger_id,
                scenario,
                evaluator_epoch,
                verifier_digest,
                cohort,
                "heldout",
                "deterministic comparison passed; held-out matched check is incomplete",
                trials,
                cost,
                active.heldout_pairs - len(heldout),
                active,
                screen_mean,
                screen_mean,
                screen_mean,
            )
        if accepted and heldout and fmean(trial.delta for trial in heldout) < active.min_effect:
            return _artifact(
                incumbent_id,
                challenger_id,
                scenario,
                evaluator_epoch,
                verifier_digest,
                cohort,
                "rejected",
                "complete",
                "held-out matched effect did not meet the minimum",
                trials,
                screen_mean,
                screen_mean,
                screen_mean,
                cost,
                0,
                active,
            )
        return _artifact(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "accepted" if accepted else "rejected",
            "complete",
            "deterministic matched effect met the minimum"
            if accepted
            else "deterministic matched effect did not meet the minimum",
            trials,
            screen_mean,
            screen_mean,
            screen_mean,
            cost,
            0,
            active,
        )

    screen_count = len(screen)
    if screen_count < active.initial_screen_pairs:
        return _needs_more(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "screen",
            "initial matched screen is incomplete",
            trials,
            cost,
            active.initial_screen_pairs - screen_count,
            active,
            screen_mean,
            screen_low,
            screen_high,
        )

    if screen_high is not None and screen_high < active.min_effect:
        return _artifact(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "rejected",
            "complete",
            "upper confidence bound is below the minimum effect",
            trials,
            screen_mean,
            screen_low,
            screen_high,
            cost,
            0,
            active,
        )

    confirmation_effects = [trial.delta for trial in confirmation]
    mean_effect, low, high = _effect_interval(confirmation_effects, active)
    confirmation_count = len(confirmation)
    if confirmation_count < active.min_confirmation_pairs:
        return _needs_more(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "confirmation",
            "screen passed but minimum confirmation evidence is incomplete",
            trials,
            cost,
            min(active.confirmation_batch_size, active.min_confirmation_pairs - confirmation_count),
            active,
            mean_effect,
            low,
            high,
        )

    if low is not None and low >= active.min_effect:
        if active.heldout_pairs > len(heldout):
            return _needs_more(
                incumbent_id,
                challenger_id,
                scenario,
                evaluator_epoch,
                verifier_digest,
                cohort,
                "heldout",
                "confirmation passed; held-out matched check is incomplete",
                trials,
                cost,
                active.heldout_pairs - len(heldout),
                active,
                mean_effect,
                low,
                high,
            )
        if heldout and fmean(trial.delta for trial in heldout) < active.min_effect:
            return _artifact(
                incumbent_id,
                challenger_id,
                scenario,
                evaluator_epoch,
                verifier_digest,
                cohort,
                "rejected",
                "complete",
                "held-out matched effect did not meet the minimum",
                trials,
                mean_effect,
                low,
                high,
                cost,
                0,
                active,
            )
        return _artifact(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "accepted",
            "complete",
            "lower confidence bound met the minimum effect",
            trials,
            mean_effect,
            low,
            high,
            cost,
            0,
            active,
        )

    if confirmation_count >= active.max_confirmation_pairs:
        return _artifact(
            incumbent_id,
            challenger_id,
            scenario,
            evaluator_epoch,
            verifier_digest,
            cohort,
            "inconclusive",
            "complete",
            "confirmation budget exhausted while uncertainty overlapped the minimum effect",
            trials,
            mean_effect,
            low,
            high,
            cost,
            0,
            active,
        )

    return _needs_more(
        incumbent_id,
        challenger_id,
        scenario,
        evaluator_epoch,
        verifier_digest,
        cohort,
        "confirmation",
        "uncertainty warrants another matched confirmation batch",
        trials,
        cost,
        min(active.confirmation_batch_size, active.max_confirmation_pairs - confirmation_count),
        active,
        mean_effect,
        low,
        high,
    )


def run_adaptive_confirmation(
    *,
    incumbent_id: str,
    challenger_id: str,
    scenario: str,
    evaluator_epoch: str,
    verifier_digest: str,
    cohort: str,
    fixtures: list[str],
    seeds: list[int],
    executor: TrialExecutor,
    protocol: TrainingPromotionProtocol | None = None,
) -> TrainingPromotionArtifact:
    """Collect only the matched trials requested by the current decision."""

    if not fixtures or not seeds:
        raise ValueError("adaptive confirmation requires fixtures and seeds")
    active = protocol or TrainingPromotionProtocol()
    trials: list[TrainingPromotionTrial] = []
    trial_pairs = list(dict.fromkeys((fixture, seed) for fixture in fixtures for seed in seeds))
    cursor = 0

    def evaluate_collected() -> TrainingPromotionArtifact:
        return evaluate_training_promotion(
            trials,
            incumbent_id=incumbent_id,
            challenger_id=challenger_id,
            scenario=scenario,
            evaluator_epoch=evaluator_epoch,
            verifier_digest=verifier_digest,
            cohort=cohort,
            protocol=active,
        )

    while True:
        artifact = evaluate_collected()
        if artifact.decision != "needs_more_trials":
            return artifact
        if artifact.phase == "complete":
            raise RuntimeError("needs_more_trials artifact cannot be complete")
        for _ in range(artifact.next_trial_count):
            if cursor >= len(trial_pairs):
                return _artifact(
                    incumbent_id,
                    challenger_id,
                    scenario,
                    evaluator_epoch,
                    verifier_digest,
                    cohort,
                    "invalid",
                    "complete",
                    "adaptive confirmation exhausted distinct fixture/seed pairs",
                    trials,
                    artifact.mean_effect,
                    artifact.confidence_low,
                    artifact.confidence_high,
                    artifact.evaluation_cost,
                    0,
                    active,
                )
            fixture, seed = trial_pairs[cursor]
            cursor += 1
            try:
                trial = executor(artifact.phase, seed, fixture)
            except Exception as exc:
                trials.append(
                    _infrastructure_trial(
                        cursor=cursor,
                        incumbent_id=incumbent_id,
                        challenger_id=challenger_id,
                        scenario=scenario,
                        evaluator_epoch=evaluator_epoch,
                        verifier_digest=verifier_digest,
                        cohort=cohort,
                        fixture=fixture,
                        seed=seed,
                        lane=artifact.phase,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                return evaluate_collected()

            mismatches: list[str] = []
            if not isinstance(trial, TrainingPromotionTrial):
                mismatches.append(f"result type {type(trial).__name__!r}")
            else:
                if trial.lane != artifact.phase:
                    mismatches.append(f"lane {trial.lane!r} (expected {artifact.phase!r})")
                if trial.fixture != fixture:
                    mismatches.append(f"fixture {trial.fixture!r} (expected {fixture!r})")
                if trial.seed != seed:
                    mismatches.append(f"seed {trial.seed!r} (expected {seed!r})")
            if mismatches:
                trials.append(
                    _infrastructure_trial(
                        cursor=cursor,
                        incumbent_id=incumbent_id,
                        challenger_id=challenger_id,
                        scenario=scenario,
                        evaluator_epoch=evaluator_epoch,
                        verifier_digest=verifier_digest,
                        cohort=cohort,
                        fixture=fixture,
                        seed=seed,
                        lane=artifact.phase,
                        error="executor returned mismatched requested trial identity: " + ", ".join(mismatches),
                    )
                )
                return evaluate_collected()
            trials.append(trial)


def _infrastructure_trial(
    *,
    cursor: int,
    incumbent_id: str,
    challenger_id: str,
    scenario: str,
    evaluator_epoch: str,
    verifier_digest: str,
    cohort: str,
    fixture: str,
    seed: int,
    lane: TrialLane,
    error: str,
) -> TrainingPromotionTrial:
    """Record fail-closed evidence for an executor protocol failure."""

    return TrainingPromotionTrial(
        trial_id=f"infrastructure-error-{cursor}",
        incumbent_id=incumbent_id,
        challenger_id=challenger_id,
        scenario=scenario,
        evaluator_epoch=evaluator_epoch,
        verifier_digest=verifier_digest,
        cohort=cohort,
        fixture=fixture,
        # The collector receives fixture identifiers, not an expected digest
        # manifest.  This digest identifies the requested fixture without
        # claiming to validate an executor-specific content-digest contract.
        fixture_digest=stable_digest(fixture),
        seed=seed,
        lane=lane,
        incumbent_score=0.0,
        challenger_score=0.0,
        infrastructure_error=error,
    )


def _validate_trial_set(
    trials: list[TrainingPromotionTrial],
    *,
    incumbent_id: str,
    challenger_id: str,
    scenario: str,
    evaluator_epoch: str,
    verifier_digest: str,
    cohort: str,
    dimension_tolerance: float,
) -> str | None:
    seen_trial_ids: set[str] = set()
    seen_pair_keys: set[str] = set()
    for trial in trials:
        if trial.trial_id in seen_trial_ids or trial.pair_key in seen_pair_keys:
            return f"duplicate matched trial: {trial.trial_id}"
        seen_trial_ids.add(trial.trial_id)
        seen_pair_keys.add(trial.pair_key)
        expected = (
            trial.incumbent_id == incumbent_id
            and trial.challenger_id == challenger_id
            and trial.scenario == scenario
            and trial.evaluator_epoch == evaluator_epoch
            and trial.verifier_digest == verifier_digest
            and trial.cohort == cohort
        )
        if not expected:
            return "trial identity, evaluator epoch, verifier, or cohort mismatch"
        if trial.infrastructure_error:
            return f"infrastructure error: {trial.infrastructure_error}"
        scalar_values = (
            trial.incumbent_score,
            trial.challenger_score,
            trial.evaluation_cost,
            *trial.incumbent_dimensions.values(),
            *trial.challenger_dimensions.values(),
        )
        if any(not math.isfinite(value) for value in scalar_values):
            return "matched trial contains a non-finite score, cost, or dimension"
        if trial.incumbent_valid and not trial.challenger_valid:
            return "challenger validity regressed"
        if not trial.incumbent_valid or not trial.challenger_valid:
            return "matched comparison requires both arms to be valid"
        if trial.incumbent_parse_ok and not trial.challenger_parse_ok:
            return "challenger parse validity regressed"
        if not trial.incumbent_parse_ok or not trial.challenger_parse_ok:
            return "matched comparison requires both arms to parse successfully"
        for dimension, incumbent_value in trial.incumbent_dimensions.items():
            challenger_value = trial.challenger_dimensions.get(dimension)
            if challenger_value is None:
                return f"challenger omitted required dimension: {dimension}"
            if challenger_value < incumbent_value - dimension_tolerance:
                return f"challenger regressed required dimension: {dimension}"
    return None


def _effect_interval(
    effects: list[float],
    protocol: TrainingPromotionProtocol,
) -> tuple[float | None, float | None, float | None]:
    if not effects:
        return None, None, None
    # Promotion may be inspected after every additional pair between the minimum
    # and maximum sample sizes.  Reserve the configured family-wise error rate
    # across all of those possible acceptance looks.
    max_looks = protocol.max_confirmation_pairs - protocol.min_confirmation_pairs + 1
    average, low, high = paired_confidence_interval(
        effects,
        protocol.confidence_z,
        max_looks=max_looks,
    )
    return (
        round(average, 12) if average is not None else None,
        round(low, 12) if low is not None else None,
        round(high, 12) if high is not None else None,
    )


def _needs_more(
    incumbent_id: str,
    challenger_id: str,
    scenario: str,
    evaluator_epoch: str,
    verifier_digest: str,
    cohort: str,
    phase: Literal["screen", "confirmation", "heldout"],
    reason: str,
    trials: list[TrainingPromotionTrial],
    cost: float,
    next_trial_count: int,
    protocol: TrainingPromotionProtocol,
    mean_effect: float | None = None,
    low: float | None = None,
    high: float | None = None,
) -> TrainingPromotionArtifact:
    return _artifact(
        incumbent_id,
        challenger_id,
        scenario,
        evaluator_epoch,
        verifier_digest,
        cohort,
        "needs_more_trials",
        phase,
        reason,
        trials,
        mean_effect,
        low,
        high,
        cost,
        next_trial_count,
        protocol,
    )


def _artifact(
    incumbent_id: str,
    challenger_id: str,
    scenario: str,
    evaluator_epoch: str,
    verifier_digest: str,
    cohort: str,
    decision: PromotionDecision,
    phase: Literal["screen", "confirmation", "heldout", "complete"],
    reason: str,
    trials: list[TrainingPromotionTrial],
    mean_effect: float | None,
    low: float | None,
    high: float | None,
    cost: float,
    next_trial_count: int,
    protocol: TrainingPromotionProtocol,
) -> TrainingPromotionArtifact:
    return TrainingPromotionArtifact(
        incumbent_id=incumbent_id,
        challenger_id=challenger_id,
        scenario=scenario,
        evaluator_epoch=evaluator_epoch,
        verifier_digest=verifier_digest,
        cohort=cohort,
        decision=decision,
        phase=phase,
        reason=reason,
        trials=trials,
        mean_effect=mean_effect,
        confidence_low=low,
        confidence_high=high,
        evaluation_cost=cost,
        next_trial_count=next_trial_count,
        protocol=protocol,
    )


__all__ = [
    "PromotionDecision",
    "ProtocolMode",
    "TrainingPromotionArtifact",
    "TrainingPromotionProtocol",
    "TrainingPromotionTrial",
    "TrialExecutor",
    "TrialLane",
    "evaluate_training_promotion",
    "run_adaptive_confirmation",
]
