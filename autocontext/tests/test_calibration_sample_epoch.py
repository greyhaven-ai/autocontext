from __future__ import annotations

from autocontext.analytics.calibration import (
    CalibrationRound,
    CalibrationSample,
    compute_round_mixed_epoch,
)


def test_round_mixed_epoch_flag() -> None:
    s1 = CalibrationSample(sample_id="a", run_id="r1", scenario="s", evaluator_epoch="e1")
    s2 = CalibrationSample(sample_id="b", run_id="r2", scenario="s", evaluator_epoch="e1")
    s3 = CalibrationSample(sample_id="c", run_id="r3", scenario="s", evaluator_epoch="e2")
    assert compute_round_mixed_epoch([s1, s2]) is False
    assert compute_round_mixed_epoch([s1, s2, s3]) is True
    # None is its own evaluator class: known + unknown spans two classes.
    s4 = CalibrationSample(sample_id="d", run_id="r4", scenario="s", evaluator_epoch=None)
    s5 = CalibrationSample(sample_id="e", run_id="r5", scenario="s", evaluator_epoch=None)
    assert compute_round_mixed_epoch([s1, s2, s4]) is True
    # All-None is a single (unknown) class; empty aggregate is not mixed.
    assert compute_round_mixed_epoch([s4, s5]) is False
    assert compute_round_mixed_epoch([]) is False


def test_round_default_mixed_epoch_false() -> None:
    rnd = CalibrationRound(round_id="rnd", created_at="2026-07-08T00:00:00Z")
    assert rnd.mixed_epoch is False
