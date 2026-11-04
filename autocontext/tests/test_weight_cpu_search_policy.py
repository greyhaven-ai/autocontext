from __future__ import annotations

import json

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption, CpuSearchPolicy
from autocontext.weight_evolution.cpu_search_policy import (
    CpuPracticeScore,
    CpuSearchDecision,
    choose_next_option,
    choose_policy_option,
    select_best,
)


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
    for invalid in (score(None, 1).model_copy(update={"correct": True}),
                    score(None, 1).model_copy(update={"total": 5.0})):
        with pytest.raises(ValueError):
            choose_next_option(options, (invalid,))
    with pytest.raises(ValueError):
        choose_next_option((options[0].model_copy(update={"step": True}),), (score(None, 1),))


def _order(policy: CpuSearchPolicy, options: tuple[CpuSearchOption, ...], correct: int = 0) -> tuple[str, ...]:
    history = (score(None, 1),)
    selected = []
    for _ in options:
        decision = choose_policy_option(policy, options, history)
        assert decision.policy == policy and decision.policy_digest == policy.digest
        assert decision.option_id is not None
        selected.append(decision.option_id)
        history += (score(decision.option_id, correct),)
    last = choose_policy_option(policy, options, history)
    assert last.option_id is None and last.reason == "exhausted"
    return tuple(selected)


def test_explicit_adaptive_dispatch_preserves_existing_algorithm(options: tuple[CpuSearchOption, ...]) -> None:
    for history in ((score(None, 1),), (score(None, 1), score("c1", 0)), (score(None, 1), score("c1", 2))):
        assert choose_policy_option(CpuSearchPolicy(), options, history) == choose_next_option(options, history)


@pytest.mark.parametrize("policy", [CpuSearchPolicy(name="fixed-order-v1"),
                                    CpuSearchPolicy(name="seeded-random-v1", order_seed=406)])
def test_control_order_ignores_correct_counts_but_keeps_earliest_best_selection(
    options: tuple[CpuSearchOption, ...], policy: CpuSearchPolicy,
) -> None:
    bad = _order(policy, options, correct=0)
    good = _order(policy, options, correct=5)
    assert bad == good and len(set(bad)) == len(options)
    assert select_best((score(None, 5), *(score(option, 4) for option in bad))).option_id is None
    assert select_best((score(None, 1), *(score(option, 4) for option in good))).option_id == good[0]


def test_fixed_order_follows_pinned_declaration_order(options: tuple[CpuSearchOption, ...]) -> None:
    policy = CpuSearchPolicy(name="fixed-order-v1")
    assert _order(policy, options) == ("c1", "c2", "c3", "c4")
    assert _order(policy, tuple(reversed(options))) == ("c4", "c3", "c2", "c1")
    assert choose_policy_option(policy, options, (score(None, 1),)).reason == "fixed-order"


@pytest.mark.parametrize("seed,expected", [(0, ("c2", "c3", "c4", "c1")), (1, ("c1", "c3", "c2", "c4")),
                                          (406, ("c3", "c2", "c4", "c1")),
                                          (2**32 - 1, ("c2", "c1", "c4", "c3"))])
def test_random_order_has_stable_versioned_vectors_independent_of_declaration_order(
    options: tuple[CpuSearchOption, ...], seed: int, expected: tuple[str, ...],
) -> None:
    policy = CpuSearchPolicy(name="seeded-random-v1", order_seed=seed)
    assert _order(policy, options) == expected
    assert _order(policy, tuple(reversed(options))) == expected
    assert choose_policy_option(policy, options, (score(None, 1),)).reason == "random-order"


@pytest.mark.parametrize("policy", [CpuSearchPolicy(name="fixed-order-v1"),
                                    CpuSearchPolicy(name="seeded-random-v1", order_seed=406)])
def test_control_dispatch_rejects_incomplete_unknown_or_wrong_order_history(
    options: tuple[CpuSearchOption, ...], policy: CpuSearchPolicy,
) -> None:
    first = choose_policy_option(policy, options, (score(None, 1),)).option_id
    wrong = next(option.option_id for option in options if option.option_id != first)
    for history in ((score(None, 1), score(first, 1, total=4)), (score(None, 1), score("outside", 1)),
                    (score(None, 1), score(wrong, 1))):
        with pytest.raises(ValueError):
            choose_policy_option(policy, options, history)


def test_policy_and_decision_identity_cannot_be_substituted(options: tuple[CpuSearchOption, ...]) -> None:
    policy = CpuSearchPolicy(name="seeded-random-v1", order_seed=406)
    decision = choose_policy_option(policy, options, (score(None, 1),))
    for fields in ({"policy_digest": "0" * 64}, {"reason": "improvement"}, {"option_id": None}):
        with pytest.raises(ValueError):
            CpuSearchDecision.model_validate_json(json.dumps({**decision.to_dict(), **fields}))
    forged = policy.model_copy(update={"order_seed": True})
    with pytest.raises(ValueError):
        choose_policy_option(forged, options, (score(None, 1),))
