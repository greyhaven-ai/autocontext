"""Pure revalidation core for on-demand re-score (AC-885 Slice D2a)."""

from __future__ import annotations

from pytest import approx

from autocontext.execution.rescore import revalidate_one


def _score_fn_ok(score: float, epoch: str | None):
    def fn(artifact: str) -> tuple[float | None, str | None]:
        return score, epoch

    return fn


def test_revalidated_stale_generation() -> None:
    r = revalidate_one(
        1, original_score=0.8, original_epoch="e1", active_epoch="e2", artifact="strategy text", score_fn=_score_fn_ok(0.6, "e2")
    )
    assert r.status == "revalidated"
    assert r.new_score == 0.6 and r.new_epoch == "e2"
    assert r.was_stale is True  # e1 != e2
    assert r.new_matches_active is True  # e2 == e2
    assert r.score_delta == approx(-0.2)


def test_no_active_epoch_skips() -> None:
    r = revalidate_one(1, 0.8, "e1", None, "art", _score_fn_ok(0.6, "e2"))
    assert r.status == "skipped_no_active_epoch"
    assert r.was_stale is False


def test_no_evaluator_when_score_fn_none() -> None:
    r = revalidate_one(1, 0.8, "e1", "e2", "art", None)
    assert r.status == "skipped_no_evaluator"


def test_no_evaluator_when_new_epoch_none() -> None:
    r = revalidate_one(1, 0.8, "e1", "e2", "art", _score_fn_ok(0.6, None))
    assert r.status == "skipped_no_evaluator"


def test_no_artifact_skips_only_when_none() -> None:
    # Only a MISSING artifact (None) skips; an empty-string output is a real artifact that was judged.
    r = revalidate_one(1, 0.8, "e1", "e2", None, _score_fn_ok(0.6, "e2"))
    assert r.status == "skipped_no_artifact"


def test_empty_string_artifact_is_revalidated() -> None:
    r = revalidate_one(1, 0.8, "e1", "e2", "", _score_fn_ok(0.6, "e2"))
    assert r.status == "revalidated"
    assert r.new_score == 0.6


def test_scorer_error_captured() -> None:
    def boom(artifact: str) -> tuple[float | None, str | None]:
        raise RuntimeError("provider down")

    r = revalidate_one(1, 0.8, "e1", "e2", "art", boom)
    assert r.status == "error"
    assert "provider down" in r.reason


def test_not_stale_when_epochs_match() -> None:
    r = revalidate_one(1, 0.8, "e2", "e2", "art", _score_fn_ok(0.7, "e2"))
    assert r.status == "revalidated"
    assert r.was_stale is False
    assert r.new_matches_active is True


def test_derived_fields_none_safe() -> None:
    # legacy original epoch/score None: not stale, delta None
    r = revalidate_one(1, None, None, "e2", "art", _score_fn_ok(0.5, "e2"))
    assert r.was_stale is False
    assert r.score_delta is None
    assert r.new_matches_active is True
