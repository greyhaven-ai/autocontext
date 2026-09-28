from __future__ import annotations

import math

from autocontext.prescreen.dataset import STRUCTURAL_FEATURES, build_examples, feature_spec_hash, structural_features
from autocontext.prescreen.rounds import EligibleRound, LedgerRound


def rnd(
    row_id: int,
    loop: str,
    n: int,
    *,
    output: str,
    score: float,
    dims: dict[str, float] | None = None,
    concepts: tuple[str, ...] = (),
    family: str = "fam",
    epoch: str = "e1",
    identity: str | None = "j1",
    name: str = "task",
    created: str = "2026-09-27T00:00:00Z",
) -> LedgerRound:
    return LedgerRound(
        row_id=row_id,
        loop_id=loop,
        run_id="r",
        scenario_name=name,
        scenario_family=family,
        round_number=n,
        max_rounds=5,
        quality_threshold=0.9,
        evaluator_epoch=epoch,
        judge_identity=identity,
        task_prompt="PROMPT",
        output=output,
        required_concepts=concepts,
        score=score,
        passed=score >= 0.9,
        judge_failed=False,
        dimension_scores=dims or {},
        previous_round_number=None,
        previous_score=None,
        previous_dimension_scores={},
        created_at=created,
    )


def test_structural_features_values_follow_the_spec_order() -> None:
    previous = rnd(1, "L", 1, output="alpha beta", score=0.4, dims={"a": 0.2, "b": 0.6})
    current = rnd(2, "L", 2, output="alpha beta gamma delta", score=0.5, concepts=("gamma", "omega"))
    values = structural_features(current, previous)
    assert len(values) == len(STRUCTURAL_FEATURES) == 8
    assert values[0] == 2.0 and values[1] == 0.4
    assert math.isclose(values[2], 0.4) and values[3] == 0.2
    assert math.isclose(values[4], math.log1p(len("alpha beta gamma delta")))
    assert math.isclose(values[5], len("alpha beta gamma delta") / len("alpha beta"))
    assert math.isclose(values[6], 2 / 4)
    assert values[7] == 0.5


def test_structural_features_fall_back_to_the_score_without_dimensions() -> None:
    values = structural_features(rnd(2, "L", 2, output="x", score=0.5), rnd(1, "L", 1, output="", score=0.3))
    assert values[2] == 0.3 and values[3] == 0.3 and values[5] == 1.0 and values[7] == 1.0


def test_build_examples_filters_orders_and_labels() -> None:
    eligible = [
        EligibleRound(
            rnd(4, "B", 2, output="b2", score=0.95, created="2026-09-27T00:00:09Z"), rnd(3, "B", 1, output="b1", score=0.3)
        ),
        EligibleRound(
            rnd(2, "A", 2, output="a2", score=0.5, created="2026-09-27T00:00:05Z"), rnd(1, "A", 1, output="a1", score=0.3)
        ),
        EligibleRound(rnd(6, "C", 2, output="c2", score=0.5, family="other"), rnd(5, "C", 1, output="c1", score=0.3)),
        EligibleRound(rnd(8, "D", 2, output="d2", score=0.5, identity="j2"), rnd(7, "D", 1, output="d1", score=0.3)),
        # Another task's rubric, or pinned dimensions, give another evaluator epoch under the same judge identity.
        EligibleRound(
            rnd(10, "E", 2, output="e2", score=0.5, epoch="e2", created="2026-09-27T00:00:07Z"),
            rnd(9, "E", 1, output="e1", score=0.3),
        ),
    ]
    examples = build_examples(eligible, family="fam", judge_identity="j1")
    assert [e.row_id for e in examples] == [2, 10, 4]
    assert [e.failed for e in examples] == [True, True, False]
    assert examples[0].group == "task" and examples[0].text == "PROMPT\n\na2"
    assert [e.row_id for e in build_examples(eligible, family="fam", judge_identity="j2")] == [8]


def test_feature_spec_hash_is_stable() -> None:
    assert feature_spec_hash() == feature_spec_hash() and len(feature_spec_hash()) == 64


def test_structural_features_token_jaccard_fallback_when_both_outputs_empty() -> None:
    # When both outputs tokenize to nothing (only special characters), token_jaccard should be 1.0
    current = rnd(2, "L", 2, output="!!", score=0.5)
    previous = rnd(1, "L", 1, output="??", score=0.3)
    values = structural_features(current, previous)
    assert values[6] == 1.0  # token_jaccard_with_previous
