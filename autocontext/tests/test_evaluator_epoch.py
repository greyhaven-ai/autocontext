from __future__ import annotations

from autocontext.execution.evaluator_epoch import (
    EVALUATOR_EPOCH_REBASELINE,
    are_comparable,
    compute_evaluator_epoch,
    resolve_epoch_rebaseline,
)


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
