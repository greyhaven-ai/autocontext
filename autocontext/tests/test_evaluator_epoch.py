from __future__ import annotations

import pytest

from autocontext.execution.evaluator_epoch import (
    EVALUATOR_EPOCH_REBASELINE,
    EpochBaselineDecision,
    are_comparable,
    compute_evaluator_epoch,
    resolve_epoch_rebaseline,
    served_pinned_dimensions,
)
from autocontext.execution.judge_spec import JudgeServingSpec


def test_epoch_is_deterministic_and_hashes_all_inputs() -> None:
    a = compute_evaluator_epoch("score correctness 0-1", "anthropic", "claude-sonnet-4-5")
    b = compute_evaluator_epoch("score correctness 0-1", "anthropic", "claude-sonnet-4-5")
    assert a.epoch_id == b.epoch_id
    assert len(a.epoch_id) == 64  # sha256 hex
    # each input participates in the hash
    assert compute_evaluator_epoch("other rubric", "anthropic", "claude-sonnet-4-5").epoch_id != a.epoch_id
    assert compute_evaluator_epoch("score correctness 0-1", "openai", "claude-sonnet-4-5").epoch_id != a.epoch_id
    assert compute_evaluator_epoch("score correctness 0-1", "anthropic", "gpt-5").epoch_id != a.epoch_id


def test_are_comparable_null_semantics() -> None:
    assert are_comparable("x", "x") is True
    assert are_comparable("x", "y") is False
    assert are_comparable(None, None) is True
    assert are_comparable(None, "x") is False
    assert are_comparable("x", None) is False


def test_resolve_epoch_rebaseline() -> None:
    # first round never re-baselines
    d0 = resolve_epoch_rebaseline(None, "e1", has_baseline=False)
    assert d0.rebaseline is False and d0.stale_epoch is None
    # same epoch: no re-baseline
    d1 = resolve_epoch_rebaseline("e1", "e1", has_baseline=True)
    assert d1.rebaseline is False
    # changed epoch: re-baseline, prior flagged stale
    d2 = resolve_epoch_rebaseline("e1", "e2", has_baseline=True)
    assert d2.rebaseline is True and d2.stale_epoch == "e1"
    assert EVALUATOR_EPOCH_REBASELINE == "evaluator_epoch_rebaseline"


def _spec(**changes: object) -> JudgeServingSpec:
    fields: dict[str, object] = {
        "compiled_rubric": "Score accuracy 0-1.",
        "judge_provider": "fake",
        "judge_model": "judge-a",
        "prompt_template_version": "autocontext.python.llm-judge.v1",
        "score_transformations": ("pinned-dimensions-zero-fill.v1",),
    }
    return JudgeServingSpec.model_validate({**fields, **changes})


_UNPINNED = _spec()
_PINNED = _spec(pinned_dimensions=("accuracy",))


def _decide(baseline: JudgeServingSpec, current: JudgeServingSpec, loop_pins: list[str] | None) -> EpochBaselineDecision:
    return resolve_epoch_rebaseline(
        baseline.epoch_id,
        current.epoch_id,
        has_baseline=True,
        baseline_spec=baseline.canonical_json(),
        round_spec=current.canonical_json(),
        loop_pinned_dimensions=loop_pins,
    )


@pytest.mark.parametrize(
    ("baseline", "current", "loop_pins", "rebaseline"),
    [
        pytest.param(_UNPINNED, _PINNED, ["accuracy"], False, id="loop-pinned-an-unpinned-judge"),
        pytest.param(_UNPINNED, _PINNED, None, True, id="loop-never-pinned"),
        pytest.param(_UNPINNED, _PINNED, ["other"], True, id="served-pins-are-not-the-loops"),
        pytest.param(_spec(pinned_dimensions=("style",)), _PINNED, ["accuracy"], True, id="baseline-served-other-pins"),
        pytest.param(
            _spec(pinned_dimensions=("b", "a")),
            _spec(pinned_dimensions=("a", "b")),
            ["a", "b"],
            True,
            id="baseline-served-the-pins-in-another-order",
        ),
    ],
)
def test_only_the_loop_pinning_an_unpinned_judge_keeps_a_changed_epoch_comparable(
    baseline: JudgeServingSpec,
    current: JudgeServingSpec,
    loop_pins: list[str] | None,
    rebaseline: bool,
) -> None:
    decision = _decide(baseline, current, loop_pins)
    assert (decision.rebaseline, decision.stale_epoch) == (rebaseline, baseline.epoch_id if rebaseline else None)


# One changed value per serving field. A field added to JudgeServingSpec fails here until it is listed.
_OTHER_SERVING_VALUES: dict[str, object] = {
    "compiled_rubric": "Score style 0-1.",
    "judge_provider": "other",
    "judge_model": "judge-b",
    "prompt_template_version": "autocontext.python.llm-judge.v2",
    "serving_examples": ("**Example 1** — Score: 0.9",),
    "score_transformations": ("other-transformation.v1",),
    "extension_fingerprint": "handler-fingerprint",
    "evaluation_context_hash": "context-hash",
}


@pytest.mark.parametrize("field", sorted(set(JudgeServingSpec.model_fields) - {"schema_version", "pinned_dimensions"}))
def test_any_other_serving_change_alongside_the_loop_pins_rebaselines(field: str) -> None:
    changed = _spec(pinned_dimensions=("accuracy",), **{field: _OTHER_SERVING_VALUES[field]})
    assert _decide(_UNPINNED, changed, ["accuracy"]).rebaseline is True


@pytest.mark.parametrize(
    ("baseline_epoch", "baseline_spec", "round_epoch", "round_spec"),
    [
        pytest.param(_UNPINNED.epoch_id, None, _PINNED.epoch_id, _PINNED.canonical_json(), id="baseline-spec-unknown"),
        pytest.param(None, _UNPINNED.canonical_json(), _PINNED.epoch_id, _PINNED.canonical_json(), id="baseline-epoch-unknown"),
        pytest.param(
            "f" * 64, _UNPINNED.canonical_json(), _PINNED.epoch_id, _PINNED.canonical_json(), id="baseline-spec-not-its-epoch"
        ),
        pytest.param(_UNPINNED.epoch_id, _UNPINNED.canonical_json(), _PINNED.epoch_id, None, id="round-spec-unknown"),
        pytest.param(
            _UNPINNED.epoch_id, _UNPINNED.canonical_json(), "f" * 64, _PINNED.canonical_json(), id="round-spec-not-its-epoch"
        ),
        pytest.param(_UNPINNED.epoch_id, _UNPINNED.canonical_json(), _PINNED.epoch_id, "{not json", id="malformed-spec"),
    ],
)
def test_unverifiable_specifications_never_exempt_an_epoch_change(
    baseline_epoch: str | None,
    baseline_spec: str | None,
    round_epoch: str,
    round_spec: str | None,
) -> None:
    decision = resolve_epoch_rebaseline(
        baseline_epoch,
        round_epoch,
        has_baseline=True,
        baseline_spec=baseline_spec,
        round_spec=round_spec,
        loop_pinned_dimensions=["accuracy"],
    )
    assert (decision.rebaseline, decision.stale_epoch) == (True, baseline_epoch)


def test_served_pinned_dimensions_come_only_from_a_verified_specification() -> None:
    declared = _spec(pinned_dimensions=("clarity", "accuracy"))
    assert served_pinned_dimensions(declared.canonical_json(), declared.epoch_id) == ("clarity", "accuracy")
    assert served_pinned_dimensions(declared.canonical_json(), "f" * 64) == ()
    assert served_pinned_dimensions(None, declared.epoch_id) == ()
    assert served_pinned_dimensions(_UNPINNED.canonical_json(), _UNPINNED.epoch_id) == ()
