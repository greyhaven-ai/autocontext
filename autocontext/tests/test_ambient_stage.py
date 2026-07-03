from __future__ import annotations

from autocontext.ambient.stage import STAGE_NAMES, AutoPauseBreaker, NoOpStage, StageResult


def test_stage_names_are_the_five_spec_stages() -> None:
    assert STAGE_NAMES == ("ingest", "curate", "advise", "train", "evaluate")


def test_breaker_pauses_after_threshold_consecutive_failures() -> None:
    breaker = AutoPauseBreaker(threshold=3)
    for _ in range(2):
        breaker.record(StageResult(processed=0, errors=1))
    assert breaker.paused is False
    breaker.record_exception()
    assert breaker.paused is True


def test_breaker_resets_on_success() -> None:
    breaker = AutoPauseBreaker(threshold=2)
    breaker.record(StageResult(errors=1))
    breaker.record(StageResult(processed=3, errors=0))
    breaker.record(StageResult(errors=1))
    assert breaker.paused is False


def test_noop_stage_returns_empty_result() -> None:
    stage = NoOpStage(name="ingest")
    result = stage.run_once(ctx=None)  # type: ignore[arg-type]
    assert result == StageResult()
