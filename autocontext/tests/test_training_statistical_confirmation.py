from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _trial(
    index: int,
    delta: float,
    *,
    lane: str = "confirmation",
    evaluator_epoch: str = "eval-7",
    challenger_valid: bool = True,
    infrastructure_error: str | None = None,
) -> Any:
    from autocontext.training.statistical_confirmation import TrainingPromotionTrial

    return TrainingPromotionTrial(
        trial_id=f"trial-{lane}-{index}",
        incumbent_id="checkpoint-1",
        challenger_id="checkpoint-2",
        scenario="grid_ctf",
        evaluator_epoch=evaluator_epoch,
        verifier_digest="sha256:verifier",
        cohort="cohort-a",
        fixture=f"fixture-{index}",
        fixture_digest=f"sha256:fixture-{index}",
        seed=index,
        lane=lane,
        incumbent_score=0.5,
        challenger_score=0.5 + delta,
        challenger_valid=challenger_valid,
        incumbent_dimensions={"safety": 1.0},
        challenger_dimensions={"safety": 1.0},
        evaluation_cost=0.25,
        infrastructure_error=infrastructure_error,
    )


def _evaluate(trials: list[Any], **protocol_overrides: Any) -> Any:
    from autocontext.training.statistical_confirmation import (
        TrainingPromotionProtocol,
        evaluate_training_promotion,
    )

    protocol = TrainingPromotionProtocol(**protocol_overrides)
    return evaluate_training_promotion(
        trials,
        incumbent_id="checkpoint-1",
        challenger_id="checkpoint-2",
        scenario="grid_ctf",
        evaluator_epoch="eval-7",
        verifier_digest="sha256:verifier",
        cohort="cohort-a",
        protocol=protocol,
    )


def test_deterministic_mode_rejects_tiny_increase_and_accepts_clear_win() -> None:
    tiny = _evaluate([_trial(1, 0.001, lane="screen")], mode="deterministic", min_effect=0.01)
    clear = _evaluate([_trial(1, 0.1, lane="screen")], mode="deterministic", min_effect=0.01)

    assert tiny.decision == "rejected"
    assert clear.decision == "accepted"
    assert clear.confidence_low == clear.confidence_high == clear.mean_effect


def test_adaptive_protocol_expands_near_tie_and_stops_clear_results_early() -> None:
    screen_win = [_trial(1, 0.1, lane="screen"), _trial(2, 0.1, lane="screen")]
    needs_confirmation = _evaluate(screen_win, min_effect=0.02)
    assert needs_confirmation.decision == "needs_more_trials"
    assert needs_confirmation.phase == "confirmation"
    assert needs_confirmation.next_trial_count == 2

    accepted = _evaluate(
        [*screen_win, _trial(3, 0.1), _trial(4, 0.1)],
        min_effect=0.02,
    )
    assert accepted.decision == "accepted"
    assert accepted.mean_effect == 0.1

    rejected = _evaluate(
        [_trial(1, -0.1, lane="screen"), _trial(2, -0.1, lane="screen")],
        min_effect=0.02,
    )
    assert rejected.decision == "rejected"
    assert "upper confidence bound" in rejected.reason


def test_noisy_near_tie_becomes_inconclusive_at_budget_and_replays() -> None:
    from autocontext.training.statistical_confirmation import TrainingPromotionArtifact

    trials = [
        _trial(1, -0.01, lane="screen"),
        _trial(2, 0.03, lane="screen"),
        _trial(3, -0.02),
        _trial(4, 0.04),
    ]
    result = _evaluate(
        trials,
        min_effect=0.01,
        min_confirmation_pairs=4,
        max_confirmation_pairs=4,
    )

    assert result.decision == "inconclusive"
    assert result.evaluation_cost == 1.0
    restored = TrainingPromotionArtifact.from_dict(result.to_dict())
    replayed = _evaluate(
        restored.trials,
        min_effect=0.01,
        min_confirmation_pairs=4,
        max_confirmation_pairs=4,
    )
    assert replayed.to_dict() == restored.to_dict()


def test_validity_evaluator_and_infrastructure_failures_are_distinct() -> None:
    invalid = _evaluate([_trial(1, 0.5, lane="screen", challenger_valid=False)])
    mismatch = _evaluate([_trial(1, 0.5, lane="screen", evaluator_epoch="eval-8")])
    infrastructure = _evaluate([_trial(1, 0.0, lane="screen", infrastructure_error="worker lost")])

    assert invalid.decision == "invalid"
    assert "validity regressed" in invalid.reason
    assert mismatch.decision == "invalid"
    assert "evaluator epoch" in mismatch.reason
    assert infrastructure.decision == "infrastructure_error"


def test_dimensional_regression_is_binding_even_when_average_improves() -> None:
    trial = _trial(1, 0.5, lane="screen")
    trial = trial.model_copy(update={"challenger_dimensions": {"safety": 0.8}})

    result = _evaluate([trial], dimension_regression_tolerance=0.0)

    assert result.decision == "invalid"
    assert "required dimension: safety" in result.reason


def test_collection_adds_trials_only_while_uncertainty_requires_them() -> None:
    from autocontext.training.statistical_confirmation import (
        TrainingPromotionTrial,
        run_adaptive_confirmation,
    )

    calls: list[tuple[str, int, str]] = []

    def execute(lane: str, seed: int, fixture: str) -> TrainingPromotionTrial:
        calls.append((lane, seed, fixture))
        return _trial(seed, -0.1, lane=lane)

    result = run_adaptive_confirmation(
        incumbent_id="checkpoint-1",
        challenger_id="checkpoint-2",
        scenario="grid_ctf",
        evaluator_epoch="eval-7",
        verifier_digest="sha256:verifier",
        cohort="cohort-a",
        fixtures=["fixture-1", "fixture-2", "fixture-3", "fixture-4"],
        seeds=[1, 2, 3, 4],
        executor=execute,
    )

    assert result.decision == "rejected"
    assert len(calls) == 2


def test_training_runner_persists_replayable_deterministic_gate(tmp_path: Path) -> None:
    from unittest.mock import patch

    from autocontext.training.runner import ExperimentOutcome, TrainingConfig, TrainingRunner
    from autocontext.training.statistical_confirmation import (
        TrainingPromotionArtifact,
        evaluate_training_promotion,
    )

    data_path = tmp_path / "data.jsonl"
    data_path.write_text("{}\n", encoding="utf-8")
    runner = TrainingRunner(
        TrainingConfig(
            scenario="grid_ctf",
            data_path=data_path,
            promotion_mode="deterministic",
            promotion_min_effect=0.01,
        ),
        work_dir=tmp_path / "workspace",
    )
    runner._best_score = 0.5
    runner._best_valid_rate = 1.0
    runner._best_experiment_index = 0
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "=== TRAINING SUMMARY ===\n"
            "avg_score: 0.501\nvalid_rate: 1.0\npeak_memory_mb: 10\ntraining_seconds: 1\n"
            "========================\n"
        ),
        stderr="",
    )

    with patch.object(runner, "_run_experiment_subprocess", return_value=completed):
        result = runner._execute_experiment(1)

    assert result.outcome == ExperimentOutcome.DISCARDED
    assert result.promotion_decision == "rejected"
    assert result.promotion_artifact_path is not None
    persisted = TrainingPromotionArtifact.from_dict(
        json.loads(result.promotion_artifact_path.read_text(encoding="utf-8"))
    )
    replayed = evaluate_training_promotion(
        persisted.trials,
        incumbent_id=persisted.incumbent_id,
        challenger_id=persisted.challenger_id,
        scenario=persisted.scenario,
        evaluator_epoch=persisted.evaluator_epoch,
        verifier_digest=persisted.verifier_digest,
        cohort=persisted.cohort,
        protocol=persisted.protocol,
    )
    assert replayed.to_dict() == persisted.to_dict()


def test_training_runner_adaptive_mode_requires_trial_executor(tmp_path: Path) -> None:
    from autocontext.training.runner import TrainingConfig, TrainingRunner

    data_path = tmp_path / "data.jsonl"
    data_path.write_text("{}\n", encoding="utf-8")
    runner = TrainingRunner(
        TrainingConfig(scenario="grid_ctf", data_path=data_path, promotion_mode="adaptive"),
        work_dir=tmp_path / "workspace",
    )
    runner._best_score = 0.5
    runner._best_valid_rate = 1.0
    runner._best_experiment_index = 0

    artifact = runner._decide_checkpoint_promotion(
        experiment_index=1,
        summary={"avg_score": 0.9, "valid_rate": 1.0},
        checkpoint_dir=tmp_path / "checkpoint",
    )

    assert artifact.decision == "infrastructure_error"
    assert "executor is not configured" in artifact.reason
