"""Trainer-facing checkpoint promotion orchestration (AC-976)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Protocol

from autocontext.training.statistical_confirmation import (
    TrainingPromotionArtifact,
    TrainingPromotionProtocol,
    TrainingPromotionTrial,
    TrialExecutor,
    evaluate_training_promotion,
    run_adaptive_confirmation,
)
from autocontext.util.json_io import write_json


class TrainingPromotionSettings(Protocol):
    scenario: str
    data_path: Path
    seed: int
    promotion_mode: Literal["deterministic", "adaptive"]
    promotion_min_effect: float
    promotion_initial_screen_pairs: int
    promotion_min_confirmation_pairs: int
    promotion_max_confirmation_pairs: int
    promotion_confirmation_batch_size: int
    promotion_heldout_pairs: int
    promotion_confidence_z: float
    promotion_dimension_regression_tolerance: float
    promotion_evaluator_epoch: str
    promotion_verifier_digest: str
    promotion_trial_cohort: str
    promotion_fixtures: tuple[str, ...]
    promotion_seeds: tuple[int, ...]


def decide_training_checkpoint_promotion(
    settings: TrainingPromotionSettings,
    *,
    experiment_index: int,
    summary: dict[str, float],
    checkpoint_dir: Path,
    best_experiment_index: int,
    best_score: float,
    best_valid_rate: float,
    executor: TrialExecutor | None,
) -> TrainingPromotionArtifact:
    protocol = _promotion_protocol(settings)
    challenger_id = f"experiment-{experiment_index}:{checkpoint_dir}"
    if best_experiment_index < 0:
        return TrainingPromotionArtifact(
            incumbent_id="trainer-baseline",
            challenger_id=challenger_id,
            scenario=settings.scenario,
            evaluator_epoch=settings.promotion_evaluator_epoch,
            verifier_digest=settings.promotion_verifier_digest,
            cohort=settings.promotion_trial_cohort,
            decision="accepted",
            phase="complete",
            reason="first successful experiment establishes the trainer-local baseline",
            trials=[],
            mean_effect=None,
            confidence_low=None,
            confidence_high=None,
            evaluation_cost=0.0,
            next_trial_count=0,
            protocol=protocol,
        )

    incumbent_id = f"experiment-{best_experiment_index}"
    if settings.promotion_mode == "adaptive":
        if executor is None:
            return _missing_executor_artifact(settings, incumbent_id, challenger_id, protocol)
        return run_adaptive_confirmation(
            incumbent_id=incumbent_id,
            challenger_id=challenger_id,
            scenario=settings.scenario,
            evaluator_epoch=settings.promotion_evaluator_epoch,
            verifier_digest=settings.promotion_verifier_digest,
            cohort=settings.promotion_trial_cohort,
            fixtures=list(settings.promotion_fixtures),
            seeds=list(settings.promotion_seeds),
            executor=executor,
            protocol=protocol,
        )

    trial = TrainingPromotionTrial(
        trial_id=f"deterministic-{experiment_index}",
        incumbent_id=incumbent_id,
        challenger_id=challenger_id,
        scenario=settings.scenario,
        evaluator_epoch=settings.promotion_evaluator_epoch,
        verifier_digest=settings.promotion_verifier_digest,
        cohort=settings.promotion_trial_cohort,
        fixture="training-summary",
        fixture_digest=_training_fixture_digest(settings.data_path),
        seed=settings.seed,
        lane="screen",
        incumbent_score=best_score,
        challenger_score=summary["avg_score"],
        incumbent_valid=best_valid_rate > 0,
        challenger_valid=summary["valid_rate"] > 0,
        incumbent_dimensions={"valid_rate": best_valid_rate},
        challenger_dimensions={"valid_rate": summary["valid_rate"]},
    )
    return evaluate_training_promotion(
        [trial],
        incumbent_id=incumbent_id,
        challenger_id=challenger_id,
        scenario=settings.scenario,
        evaluator_epoch=settings.promotion_evaluator_epoch,
        verifier_digest=settings.promotion_verifier_digest,
        cohort=settings.promotion_trial_cohort,
        protocol=protocol,
    )


def write_training_promotion_artifact(
    work_dir: Path,
    experiment_index: int,
    artifact: TrainingPromotionArtifact,
) -> Path:
    path = work_dir / "promotion" / f"experiment_{experiment_index}.json"
    write_json(path, artifact.to_dict())
    return path


def _promotion_protocol(settings: TrainingPromotionSettings) -> TrainingPromotionProtocol:
    return TrainingPromotionProtocol(
        mode=settings.promotion_mode,
        initial_screen_pairs=settings.promotion_initial_screen_pairs,
        min_confirmation_pairs=settings.promotion_min_confirmation_pairs,
        max_confirmation_pairs=settings.promotion_max_confirmation_pairs,
        confirmation_batch_size=settings.promotion_confirmation_batch_size,
        heldout_pairs=settings.promotion_heldout_pairs,
        min_effect=settings.promotion_min_effect,
        confidence_z=settings.promotion_confidence_z,
        dimension_regression_tolerance=settings.promotion_dimension_regression_tolerance,
    )


def _missing_executor_artifact(
    settings: TrainingPromotionSettings,
    incumbent_id: str,
    challenger_id: str,
    protocol: TrainingPromotionProtocol,
) -> TrainingPromotionArtifact:
    trial = TrainingPromotionTrial(
        trial_id="adaptive-executor-unavailable",
        incumbent_id=incumbent_id,
        challenger_id=challenger_id,
        scenario=settings.scenario,
        evaluator_epoch=settings.promotion_evaluator_epoch,
        verifier_digest=settings.promotion_verifier_digest,
        cohort=settings.promotion_trial_cohort,
        fixture="unavailable",
        fixture_digest="unavailable",
        seed=settings.seed,
        lane="screen",
        incumbent_score=0.0,
        challenger_score=0.0,
        infrastructure_error="adaptive promotion trial executor is not configured",
    )
    return evaluate_training_promotion(
        [trial],
        incumbent_id=incumbent_id,
        challenger_id=challenger_id,
        scenario=settings.scenario,
        evaluator_epoch=settings.promotion_evaluator_epoch,
        verifier_digest=settings.promotion_verifier_digest,
        cohort=settings.promotion_trial_cohort,
        protocol=protocol,
    )


def _training_fixture_digest(data_path: Path) -> str:
    try:
        return hashlib.sha256(data_path.read_bytes()).hexdigest()
    except OSError:
        return hashlib.sha256(str(data_path).encode("utf-8")).hexdigest()


__all__ = [
    "TrainingPromotionSettings",
    "decide_training_checkpoint_promotion",
    "write_training_promotion_artifact",
]
