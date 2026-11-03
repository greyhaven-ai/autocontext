from __future__ import annotations

import json

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore, choose_next_option, select_best


@pytest.fixture
def options() -> tuple[CpuSearchOption, ...]:
    return tuple(CpuSearchOption(option_id=name, mechanism="C", direction=direction, step=step, context=context)
                 for name, direction, step, context in (("c1", "positive", 1, "hint"),
                                                        ("c2", "positive", 2, "hint hint"),
                                                        ("c3", "negative", 1, "counter"),
                                                        ("c4", "negative", 2, "counter counter")))


def score(option_id: str | None, correct: int, total: int = 5) -> CpuPracticeScore:
    return CpuPracticeScore(option_id=option_id, correct=correct, total=total)


def test_measured_improvement_changes_next_proposal(options: tuple[CpuSearchOption, ...]) -> None:
    baseline = (score(None, 1),)
    initial = choose_next_option(options, baseline)
    assert (initial.option_id, initial.reason) == ("c1", "initial")
    improved = choose_next_option(options, (*baseline, score("c1", 2)))
    regressed = choose_next_option(options, (*baseline, score("c1", 0)))
    tied = choose_next_option(options, (*baseline, score("c1", 1)))
    assert (improved.option_id, improved.reason) == ("c2", "improvement")
    assert (regressed.option_id, regressed.reason) == ("c3", "switch")
    assert (tied.option_id, tied.reason) == ("c3", "switch")
    assert improved.history_digest != regressed.history_digest
    assert initial.history_digest == canonical_digest([row.to_dict() for row in baseline])


def test_harmful_later_candidate_does_not_displace_earlier_winner(options: tuple[CpuSearchOption, ...]) -> None:
    history = (score(None, 1), score("c1", 4), score("c2", 2))
    assert select_best(history).option_id == "c1"
    assert choose_next_option(options, history).option_id == "c3"
    # An improvement over the immediately previous loser is not an improvement
    # over the retained best, so it must not earn another direction step.
    history += (score("c3", 3),)
    assert choose_next_option(options, history).reason == "fallback"
    assert select_best(history).option_id == "c1"


def test_earliest_winner_and_baseline_are_retained_on_ties() -> None:
    assert select_best((score(None, 3), score("c1", 3))).option_id is None
    assert select_best((score(None, 1), score("c1", 3), score("c2", 3))).option_id == "c1"


def test_finite_search_does_not_retry_any_option(options: tuple[CpuSearchOption, ...]) -> None:
    history = (score(None, 1),)
    visited = []
    while True:
        decision = choose_next_option(options, history)
        if decision.option_id is None:
            assert decision.reason == "exhausted"
            break
        visited.append(decision.option_id)
        history += (score(decision.option_id, 0),)
    assert visited == ["c1", "c3", "c2", "c4"]
    assert len(visited) == len(set(visited)) == len(options)


def test_new_direction_after_best_reaches_direction_end(options: tuple[CpuSearchOption, ...]) -> None:
    decision = choose_next_option(options, (score(None, 0), score("c1", 1), score("c2", 2)))
    assert (decision.option_id, decision.reason) == ("c3", "switch")


@pytest.mark.parametrize("history", [(), (score("c1", 1),), (score(None, 1), score(None, 2)),
                                     (score(None, 1), score("c1", 2), score("c1", 3)),
                                     (score(None, 1), score("c1", 1, total=4))])
def test_selection_rejects_partial_duplicate_or_missing_baseline_history(history: tuple[CpuPracticeScore, ...]) -> None:
    with pytest.raises(ValueError):
        select_best(history)


def test_policy_rejects_unknown_and_cross_arm_options(options: tuple[CpuSearchOption, ...]) -> None:
    with pytest.raises(ValueError, match="unknown"):
        choose_next_option(options, (score(None, 1), score("outside", 2)))
    cross_arm = CpuSearchOption(option_id="w1", mechanism="W", direction="weight", step=1, candidate_id="edited")
    with pytest.raises(ValueError, match="one mechanism"):
        choose_next_option((*options, cross_arm), (score(None, 1),))
    with pytest.raises(ValueError, match="distinct"):
        choose_next_option((options[0], options[0]), (score(None, 1),))
    with pytest.raises(ValueError, match="direction/step"):
        choose_next_option((options[0], options[0].model_copy(update={"option_id": "different"})), (score(None, 1),))


@pytest.mark.parametrize("field,value", [("correct", -1), ("correct", 6), ("correct", True), ("total", 1),
                                         ("total", 17), ("audit", {"correct": 4}), ("gold", "one"),
                                         ("confirmation", "passed"), ("logits", [0, 1])])
def test_policy_score_boundary_accepts_only_complete_practice_counts(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        CpuPracticeScore.model_validate_json(json.dumps({**score(None, 1).to_dict(), field: value}))


def test_policy_revalidates_untrusted_model_copies(options: tuple[CpuSearchOption, ...]) -> None:
    forged = score(None, 1).model_copy(update={"correct": 99})
    with pytest.raises(ValueError):
        choose_next_option(options, (forged,))
    with pytest.raises(ValueError):
        select_best((forged,))
    option = options[0].model_copy(update={"candidate_id": "forbidden"})
    with pytest.raises(ValueError):
        choose_next_option((option,), (score(None, 1),))
