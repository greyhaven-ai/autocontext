from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path
from typing import Any

import pytest


def _trial(
    index: int,
    delta: float,
    *,
    lane: str = "confirmation",
    evaluator_epoch: str = "eval-7",
    incumbent_valid: bool = True,
    challenger_valid: bool = True,
    incumbent_parse_ok: bool = True,
    challenger_parse_ok: bool = True,
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
        incumbent_valid=incumbent_valid,
        challenger_valid=challenger_valid,
        incumbent_parse_ok=incumbent_parse_ok,
        challenger_parse_ok=challenger_parse_ok,
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


def test_deterministic_mode_requires_exact_configured_heldout_evidence() -> None:
    screen = _trial(1, 0.1, lane="screen")
    missing = _evaluate([screen], mode="deterministic", min_effect=0.01, heldout_pairs=1)
    accepted = _evaluate(
        [screen, _trial(2, 0.1, lane="heldout")],
        mode="deterministic",
        min_effect=0.01,
        heldout_pairs=1,
    )
    rejected = _evaluate(
        [screen, _trial(2, -0.1, lane="heldout")],
        mode="deterministic",
        min_effect=0.01,
        heldout_pairs=1,
    )

    assert missing.decision == "needs_more_trials"
    assert missing.phase == "heldout"
    assert missing.next_trial_count == 1
    assert accepted.decision == "accepted"
    assert rejected.decision == "rejected"


def test_adaptive_protocol_expands_near_tie_and_stops_clear_results_early() -> None:
    screen_win = [_trial(1, 0.1, lane="screen"), _trial(2, 0.1, lane="screen")]
    needs_confirmation = _evaluate(screen_win, min_effect=0.02)
    assert needs_confirmation.decision == "needs_more_trials"
    assert needs_confirmation.phase == "confirmation"
    assert needs_confirmation.next_trial_count == 2

    accepted = _evaluate(
        [*screen_win, *[_trial(index, 0.1) for index in range(3, 7)]],
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


def test_confirmation_evidence_over_configured_maximum_is_invalid_before_acceptance() -> None:
    trials = [
        _trial(1, 0.1, lane="screen"),
        _trial(2, 0.1, lane="screen"),
        *[_trial(index, 0.1) for index in range(3, 8)],
    ]

    result = _evaluate(
        trials,
        min_effect=0.01,
        min_confirmation_pairs=4,
        max_confirmation_pairs=4,
    )

    assert result.decision == "invalid"
    assert "exceeds the configured maximum" in result.reason
    assert result.confidence_low is None


def test_surplus_heldout_evidence_cannot_flip_rejection_to_acceptance() -> None:
    confirmation = [
        _trial(1, 0.1, lane="screen"),
        _trial(2, 0.1, lane="screen"),
        *[_trial(index, 0.1) for index in range(3, 7)],
    ]
    required_heldout = _trial(7, -0.1, lane="heldout")
    surplus_heldout = _trial(8, 0.5, lane="heldout")
    protocol = {
        "min_effect": 0.01,
        "min_confirmation_pairs": 4,
        "max_confirmation_pairs": 4,
        "heldout_pairs": 1,
    }

    control = _evaluate([*confirmation, required_heldout], **protocol)
    surplus = _evaluate([*confirmation, required_heldout, surplus_heldout], **protocol)

    assert control.decision == "rejected"
    assert surplus.decision == "invalid"
    assert "held-out evidence exceeds" in surplus.reason


def test_noisy_near_tie_becomes_inconclusive_at_budget_and_replays() -> None:
    from autocontext.training.statistical_confirmation import TrainingPromotionArtifact

    trials = [
        _trial(1, -0.01, lane="screen"),
        _trial(2, 0.03, lane="screen"),
        _trial(3, -0.02),
        _trial(4, 0.04),
        _trial(5, -0.01),
        _trial(6, 0.03),
    ]
    result = _evaluate(
        trials,
        min_effect=0.01,
        min_confirmation_pairs=4,
        max_confirmation_pairs=4,
    )

    assert result.decision == "inconclusive"
    assert result.evaluation_cost == 1.5
    restored = TrainingPromotionArtifact.from_dict(result.to_dict())
    replayed = _evaluate(
        restored.trials,
        min_effect=0.01,
        min_confirmation_pairs=4,
        max_confirmation_pairs=4,
    )
    assert replayed.to_dict() == restored.to_dict()


def test_small_sample_t_interval_does_not_accept_normal_approximation_false_positive() -> None:
    # A fixed 1.96 z interval has lower bound 0.012684 and would promote at
    # min_effect=0.01.  Student's t with sequential alpha spending correctly
    # treats four pairs as insufficient evidence.
    trials = [
        _trial(1, 0.1, lane="screen"),
        _trial(2, 0.1, lane="screen"),
        _trial(3, 0.014),
        _trial(4, 0.014),
        _trial(5, 0.034),
        _trial(6, 0.034),
    ]

    result = _evaluate(trials, min_effect=0.01)

    assert result.decision == "needs_more_trials"
    assert result.confidence_low is not None
    assert result.confidence_low < 0.01


def test_screen_pairs_do_not_count_toward_confirmation_evidence() -> None:
    screen = [_trial(1, 0.1, lane="screen"), _trial(2, 0.1, lane="screen")]

    zero_confirmation = _evaluate(screen, min_effect=0.01)
    partial_confirmation = _evaluate(
        [*screen, _trial(3, 0.03), _trial(4, 0.03)],
        min_effect=0.01,
    )

    assert zero_confirmation.decision == "needs_more_trials"
    assert zero_confirmation.phase == "confirmation"
    assert zero_confirmation.next_trial_count == 2
    assert zero_confirmation.mean_effect is None
    assert partial_confirmation.decision == "needs_more_trials"
    assert partial_confirmation.next_trial_count == 2
    assert partial_confirmation.mean_effect == 0.03


def test_same_fixture_seed_cannot_be_reused_across_lanes() -> None:
    result = _evaluate(
        [
            _trial(1, 0.1, lane="screen"),
            _trial(1, 0.1, lane="confirmation"),
        ],
        min_effect=0.01,
    )

    assert result.decision == "invalid"
    assert "duplicate matched trial" in result.reason


def test_fixture_label_alias_cannot_hide_reused_digest_and_seed() -> None:
    result = _evaluate(
        [
            _trial(1, 0.1, lane="screen"),
            _trial(1, 0.1, lane="confirmation").model_copy(update={"fixture": "fixture-one-alias"}),
        ],
        min_effect=0.01,
    )

    assert result.decision == "invalid"
    assert "duplicate matched trial" in result.reason


def test_sequential_intervals_control_false_promotions_across_repeated_looks() -> None:
    from autocontext.analytics.paired_statistics import paired_confidence_interval

    rng = random.Random(712)
    false_promotions = 0
    simulations = 500
    for _ in range(simulations):
        # The true effect is exactly the promotion threshold.  Peeking at an
        # ordinary 95% interval after every pair inflates the false-positive
        # rate; max_looks spends the error budget across all nine inspections.
        effects = [rng.gauss(0.01, 0.02) for _ in range(12)]
        for pair_count in range(4, 13):
            _, low, _ = paired_confidence_interval(
                effects[:pair_count],
                1.96,
                max_looks=9,
            )
            if low is not None and low >= 0.01:
                false_promotions += 1
                break

    assert false_promotions / simulations < 0.04


def test_validity_evaluator_and_infrastructure_failures_are_distinct() -> None:
    invalid = _evaluate([_trial(1, 0.5, lane="screen", challenger_valid=False)])
    mismatch = _evaluate([_trial(1, 0.5, lane="screen", evaluator_epoch="eval-8")])
    infrastructure = _evaluate([_trial(1, 0.0, lane="screen", infrastructure_error="worker lost")])

    assert invalid.decision == "invalid"
    assert "validity regressed" in invalid.reason
    assert mismatch.decision == "invalid"
    assert "evaluator epoch" in mismatch.reason
    assert infrastructure.decision == "infrastructure_error"


def test_both_invalid_or_unparseable_arms_cannot_be_promoted() -> None:
    invalid = _evaluate(
        [_trial(1, 0.5, lane="screen", incumbent_valid=False, challenger_valid=False)],
        mode="deterministic",
    )
    unparseable = _evaluate(
        [
            _trial(
                1,
                0.5,
                lane="screen",
                incumbent_parse_ok=False,
                challenger_parse_ok=False,
            )
        ],
        mode="deterministic",
    )

    assert invalid.decision == "invalid"
    assert "both arms" in invalid.reason
    assert unparseable.decision == "invalid"
    assert "both arms" in unparseable.reason


def test_non_finite_matched_scores_cannot_be_promoted() -> None:
    result = _evaluate(
        [_trial(1, float("nan"), lane="screen")],
        mode="deterministic",
    )

    assert result.decision == "invalid"
    assert "non-finite" in result.reason


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
        return _trial(seed, -0.1, lane=lane).model_copy(
            update={"fixture": fixture, "fixture_digest": f"sha256:{fixture}"}
        )

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


@pytest.mark.parametrize(
    ("update", "mismatched_field"),
    [
        ({"lane": "confirmation"}, "lane"),
        ({"fixture": "wrong-fixture"}, "fixture"),
        ({"seed": 101}, "seed"),
    ],
)
def test_collection_fails_closed_when_executor_returns_wrong_requested_identity(
    update: dict[str, Any],
    mismatched_field: str,
) -> None:
    from autocontext.training.statistical_confirmation import (
        TrainingPromotionTrial,
        run_adaptive_confirmation,
    )

    requested: list[tuple[str, int, str]] = []

    def execute(lane: str, seed: int, fixture: str) -> TrainingPromotionTrial:
        requested.append((lane, seed, fixture))
        trial = _trial(seed, 0.1, lane=lane)
        return trial.model_copy(update=update)

    result = run_adaptive_confirmation(
        incumbent_id="checkpoint-1",
        challenger_id="checkpoint-2",
        scenario="grid_ctf",
        evaluator_epoch="eval-7",
        verifier_digest="sha256:verifier",
        cohort="cohort-a",
        fixtures=["fixture-1", "fixture-2"],
        seeds=[1, 2],
        executor=execute,
    )

    assert requested == [("screen", 1, "fixture-1")]
    assert result.decision == "infrastructure_error"
    assert "mismatched requested trial identity" in result.reason
    assert mismatched_field in result.reason
    assert len(result.trials) == 1
    evidence = result.trials[0]
    assert evidence.lane == "screen"
    assert evidence.fixture == "fixture-1"
    assert evidence.seed == 1
    assert evidence.infrastructure_error is not None


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


def test_training_runner_discards_surplus_heldout_promotion_evidence(tmp_path: Path) -> None:
    from unittest.mock import patch

    from autocontext.training.runner import ExperimentOutcome, TrainingConfig, TrainingRunner

    artifact = _evaluate(
        [
            _trial(1, 0.1, lane="screen"),
            _trial(2, 0.1, lane="screen"),
            *[_trial(index, 0.1) for index in range(3, 7)],
            _trial(7, -0.1, lane="heldout"),
            _trial(8, 0.5, lane="heldout"),
        ],
        min_effect=0.01,
        min_confirmation_pairs=4,
        max_confirmation_pairs=4,
        heldout_pairs=1,
    )
    assert artifact.decision == "invalid"

    data_path = tmp_path / "data.jsonl"
    data_path.write_text("{}\n", encoding="utf-8")
    runner = TrainingRunner(TrainingConfig(scenario="grid_ctf", data_path=data_path), work_dir=tmp_path / "workspace")
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "=== TRAINING SUMMARY ===\n"
            "avg_score: 0.9\nvalid_rate: 1.0\npeak_memory_mb: 10\ntraining_seconds: 1\n"
            "========================\n"
        ),
        stderr="",
    )

    with (
        patch.object(runner, "_run_experiment_subprocess", return_value=completed),
        patch.object(runner, "_decide_checkpoint_promotion", return_value=artifact),
    ):
        result = runner._execute_experiment(1)

    assert result.outcome == ExperimentOutcome.DISCARDED
    assert result.promotion_decision == "invalid"
    assert result.checkpoint_path is None


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


def test_adaptive_matched_winner_becomes_incumbent_despite_lower_raw_summary(tmp_path: Path) -> None:
    from unittest.mock import patch

    from autocontext.context_bundles.models import stable_digest
    from autocontext.training.runner import ExperimentOutcome, TrainingConfig, TrainingRunner
    from autocontext.training.statistical_confirmation import TrainingPromotionTrial

    data_path = tmp_path / "data.jsonl"
    data_path.write_text("{}\n", encoding="utf-8")
    work_dir = tmp_path / "workspace"

    def execute(lane: str, seed: int, fixture: str) -> TrainingPromotionTrial:
        return TrainingPromotionTrial(
            trial_id=f"{lane}-{seed}",
            incumbent_id="experiment-0",
            challenger_id=f"experiment-1:{runner._checkpoint_dir(1)}",
            scenario="grid_ctf",
            evaluator_epoch="training-evaluator-v1",
            verifier_digest="training-summary-v1",
            cohort="trainer-local",
            fixture=fixture,
            fixture_digest=stable_digest(fixture),
            seed=seed,
            lane=lane,
            incumbent_score=0.50,
            challenger_score=0.55,
        )

    runner = TrainingRunner(
        TrainingConfig(
            scenario="grid_ctf",
            data_path=data_path,
            promotion_mode="adaptive",
            promotion_min_effect=0.01,
            promotion_min_confirmation_pairs=4,
            promotion_max_confirmation_pairs=4,
        ),
        work_dir=work_dir,
        promotion_trial_executor=execute,
    )
    runner._best_score = 0.50
    runner._best_valid_rate = 1.0
    runner._best_experiment_index = 0
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "=== TRAINING SUMMARY ===\n"
            "avg_score: 0.49\nvalid_rate: 1.0\npeak_memory_mb: 10\ntraining_seconds: 1\n"
            "========================\n"
        ),
        stderr="",
    )

    with patch.object(runner, "_run_experiment_subprocess", return_value=completed):
        result = runner._execute_experiment(1)
    runner._update_best(result)

    assert result.outcome == ExperimentOutcome.KEPT
    assert result.promotion_decision == "accepted"
    assert runner._best_experiment_index == 1
    assert runner._best_score == 0.49
