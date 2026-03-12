"""Tests for AC-197: Staged candidate validation contract.

Tests the ValidationStage ABC, StageResult, StageStatus, and ValidationPipeline
with sequential execution, early-exit, and skip semantics.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from autocontext.harness.validation import (
    StageResult,
    StageStatus,
    ValidationPipeline,
    ValidationStage,
)

# ── Concrete test stages ─────────────────────────────────────────────────


class AlwaysPassStage(ValidationStage):
    """Stage that always passes."""

    @property
    def name(self) -> str:
        return "always_pass"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        return StageResult(stage=self.order, name=self.name, status=StageStatus.PASSED, duration_ms=0.1)


class AlwaysFailStage(ValidationStage):
    """Stage that always fails with an error code."""

    @property
    def name(self) -> str:
        return "always_fail"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        return StageResult(
            stage=self.order, name=self.name, status=StageStatus.FAILED, duration_ms=0.5,
            error="intentional failure", error_code="test_fail",
        )


class SkippableStage(ValidationStage):
    """Stage that returns SKIPPED."""

    @property
    def name(self) -> str:
        return "skippable"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        return StageResult(stage=self.order, name=self.name, status=StageStatus.SKIPPED, duration_ms=0.0)


class TrackingStage(ValidationStage):
    """Stage that tracks whether it was called."""

    def __init__(self, order: int, stage_name: str = "tracking") -> None:
        super().__init__(order)
        self._stage_name = stage_name
        self.was_called = False

    @property
    def name(self) -> str:
        return self._stage_name

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        self.was_called = True
        return StageResult(stage=self.order, name=self.name, status=StageStatus.PASSED, duration_ms=0.1)


class SlowStage(ValidationStage):
    """Stage with measurable duration."""

    @property
    def name(self) -> str:
        return "slow"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        t0 = time.monotonic()
        time.sleep(0.01)  # 10ms
        duration_ms = (time.monotonic() - t0) * 1000
        return StageResult(stage=self.order, name=self.name, status=StageStatus.PASSED, duration_ms=duration_ms)


# ── StageStatus tests ────────────────────────────────────────────────────


class TestStageStatus:
    def test_status_values(self) -> None:
        assert StageStatus.PASSED.value == "passed"
        assert StageStatus.FAILED.value == "failed"
        assert StageStatus.SKIPPED.value == "skipped"

    def test_status_is_str_enum(self) -> None:
        assert isinstance(StageStatus.PASSED, str)
        assert StageStatus.PASSED == "passed"


# ── StageResult tests ────────────────────────────────────────────────────


class TestStageResult:
    def test_stage_result_passed(self) -> None:
        r = StageResult(stage=1, name="syntax", status=StageStatus.PASSED, duration_ms=1.5)
        assert r.passed is True
        assert r.status is StageStatus.PASSED
        assert r.stage == 1
        assert r.name == "syntax"
        assert r.duration_ms == 1.5
        assert r.error is None
        assert r.error_code is None

    def test_stage_result_failed_with_error_and_code(self) -> None:
        r = StageResult(
            stage=2, name="contract", status=StageStatus.FAILED, duration_ms=3.0,
            error="missing field 'action'", error_code="missing_field",
        )
        assert r.passed is False
        assert r.status is StageStatus.FAILED
        assert r.error == "missing field 'action'"
        assert r.error_code == "missing_field"

    def test_stage_result_skipped(self) -> None:
        r = StageResult(stage=3, name="edge_case", status=StageStatus.SKIPPED, duration_ms=0.0)
        assert r.passed is False  # skipped is not passed
        assert r.status is StageStatus.SKIPPED

    def test_stage_result_is_frozen(self) -> None:
        r = StageResult(stage=1, name="test", status=StageStatus.PASSED, duration_ms=0.0)
        with pytest.raises(AttributeError):
            r.status = StageStatus.FAILED  # type: ignore[misc]


# ── ValidationStage ABC tests ────────────────────────────────────────────


class TestValidationStage:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            ValidationStage(order=1)  # type: ignore[abstract]

    def test_concrete_stage_has_name_and_order(self) -> None:
        stage = AlwaysPassStage(order=1)
        assert stage.name == "always_pass"
        assert stage.order == 1

    def test_stage_run_returns_stage_result(self) -> None:
        stage = AlwaysPassStage(order=0)
        result = stage.run(candidate={"action": "move"}, scenario=None)
        assert isinstance(result, StageResult)
        assert result.passed is True


# ── ValidationPipeline tests ─────────────────────────────────────────────


class TestValidationPipeline:
    def test_empty_pipeline_returns_empty_results(self) -> None:
        """Empty pipeline is explicitly valid — vacuous truth."""
        pipeline = ValidationPipeline(stages=[])
        results = pipeline.run(candidate={}, scenario=None)
        assert results == []

    def test_empty_pipeline_all_passed_is_true(self) -> None:
        """Vacuous truth: all_passed([]) is True by design."""
        assert ValidationPipeline.all_passed([]) is True

    def test_all_stages_pass(self) -> None:
        stages = [AlwaysPassStage(order=i) for i in range(3)]
        pipeline = ValidationPipeline(stages=stages)
        results = pipeline.run(candidate={}, scenario=None)
        assert len(results) == 3
        assert all(r.passed for r in results)

    def test_early_exit_on_failure(self) -> None:
        """Later stages should NOT run when an earlier stage fails."""
        fail_stage = AlwaysFailStage(order=1)
        tracking_stage = TrackingStage(order=2, stage_name="should_not_run")
        pipeline = ValidationPipeline(stages=[
            AlwaysPassStage(order=0),
            fail_stage,
            tracking_stage,
        ])
        results = pipeline.run(candidate={}, scenario=None)

        assert len(results) == 2  # Only stages 0 and 1 ran
        assert results[0].passed is True
        assert results[1].passed is False
        assert results[1].error == "intentional failure"
        assert results[1].error_code == "test_fail"
        assert tracking_stage.was_called is False

    def test_skipped_stage_does_not_halt_pipeline(self) -> None:
        """SKIPPED stages are recorded but do not trigger early-exit."""
        tracking = TrackingStage(order=2, stage_name="after_skip")
        pipeline = ValidationPipeline(stages=[
            AlwaysPassStage(order=0),
            SkippableStage(order=1),
            tracking,
        ])
        results = pipeline.run(candidate={}, scenario=None)

        assert len(results) == 3
        assert results[1].status is StageStatus.SKIPPED
        assert tracking.was_called is True

    def test_all_passed_with_skipped_stages(self) -> None:
        """Skipped stages do not count as failures."""
        pipeline = ValidationPipeline(stages=[
            AlwaysPassStage(order=0),
            SkippableStage(order=1),
        ])
        results = pipeline.run(candidate={}, scenario=None)
        assert ValidationPipeline.all_passed(results) is True

    def test_stages_run_in_order(self) -> None:
        """Stages should run in order regardless of insertion order."""
        s2 = TrackingStage(order=2, stage_name="second")
        s0 = TrackingStage(order=0, stage_name="first")
        s1 = TrackingStage(order=1, stage_name="middle")
        pipeline = ValidationPipeline(stages=[s2, s0, s1])  # intentionally out of order
        results = pipeline.run(candidate={}, scenario=None)

        assert len(results) == 3
        assert [r.name for r in results] == ["first", "middle", "second"]

    def test_duplicate_stage_orders_preserve_insertion_order(self) -> None:
        """Stages with the same order run in insertion sequence (stable sort)."""
        a = TrackingStage(order=0, stage_name="alpha")
        b = TrackingStage(order=0, stage_name="beta")
        c = TrackingStage(order=0, stage_name="gamma")
        pipeline = ValidationPipeline(stages=[a, b, c])
        results = pipeline.run(candidate={}, scenario=None)

        assert [r.name for r in results] == ["alpha", "beta", "gamma"]

    def test_results_include_timing(self) -> None:
        pipeline = ValidationPipeline(stages=[SlowStage(order=0)])
        results = pipeline.run(candidate={}, scenario=None)
        assert len(results) == 1
        assert results[0].duration_ms > 0

    def test_failed_stage_reports_which_stage(self) -> None:
        pipeline = ValidationPipeline(stages=[
            AlwaysPassStage(order=0),
            AlwaysFailStage(order=1),
        ])
        results = pipeline.run(candidate={}, scenario=None)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert failed[0].name == "always_fail"
        assert failed[0].stage == 1

    def test_pipeline_with_single_stage(self) -> None:
        pipeline = ValidationPipeline(stages=[AlwaysPassStage(order=0)])
        results = pipeline.run(candidate={}, scenario=None)
        assert len(results) == 1
        assert results[0].passed is True

    def test_pipeline_first_stage_fails(self) -> None:
        tracking = TrackingStage(order=1)
        pipeline = ValidationPipeline(stages=[AlwaysFailStage(order=0), tracking])
        results = pipeline.run(candidate={}, scenario=None)
        assert len(results) == 1
        assert results[0].passed is False
        assert tracking.was_called is False

    def test_pipeline_passes_candidate_and_scenario_to_stages(self) -> None:
        """Verify candidate and scenario are forwarded to each stage."""

        class CapturingStage(ValidationStage):
            captured_candidate: Any = None
            captured_scenario: Any = None

            @property
            def name(self) -> str:
                return "capturing"

            def run(self, candidate: Any, scenario: Any) -> StageResult:
                self.captured_candidate = candidate
                self.captured_scenario = scenario
                return StageResult(stage=self.order, name=self.name, status=StageStatus.PASSED, duration_ms=0.0)

        stage = CapturingStage(order=0)
        candidate = {"action": "move", "x": 1}
        scenario = {"name": "grid_ctf"}
        pipeline = ValidationPipeline(stages=[stage])
        pipeline.run(candidate=candidate, scenario=scenario)

        assert stage.captured_candidate is candidate
        assert stage.captured_scenario is scenario

    def test_stage_exception_becomes_failure_with_error_code(self) -> None:
        """If a stage raises, the pipeline catches it and produces a failure with error_code."""

        class CrashingStage(ValidationStage):
            @property
            def name(self) -> str:
                return "crasher"

            def run(self, candidate: Any, scenario: Any) -> StageResult:
                raise RuntimeError("stage exploded")

        tracking = TrackingStage(order=1)
        pipeline = ValidationPipeline(stages=[CrashingStage(order=0), tracking])
        results = pipeline.run(candidate={}, scenario=None)

        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].status is StageStatus.FAILED
        assert "stage exploded" in (results[0].error or "")
        assert results[0].error_code == "stage_exception"
        assert tracking.was_called is False

    def test_pipeline_all_passed_property(self) -> None:
        """Pipeline should expose whether all stages passed."""
        pipeline = ValidationPipeline(stages=[AlwaysPassStage(order=0), AlwaysPassStage(order=1)])
        results = pipeline.run(candidate={}, scenario=None)
        assert pipeline.all_passed(results) is True

    def test_pipeline_all_passed_false_on_failure(self) -> None:
        pipeline = ValidationPipeline(stages=[AlwaysPassStage(order=0), AlwaysFailStage(order=1)])
        results = pipeline.run(candidate={}, scenario=None)
        assert pipeline.all_passed(results) is False

    def test_pipeline_failed_stage_name(self) -> None:
        """Pipeline should report which stage failed."""
        pipeline = ValidationPipeline(stages=[
            AlwaysPassStage(order=0),
            AlwaysFailStage(order=1),
            TrackingStage(order=2),
        ])
        results = pipeline.run(candidate={}, scenario=None)
        assert pipeline.failed_stage(results) == "always_fail"

    def test_pipeline_failed_stage_none_when_all_pass(self) -> None:
        pipeline = ValidationPipeline(stages=[AlwaysPassStage(order=0)])
        results = pipeline.run(candidate={}, scenario=None)
        assert pipeline.failed_stage(results) is None

    def test_imports_from_package_init(self) -> None:
        """Types should be importable from autocontext.harness.validation directly."""
        from autocontext.harness.validation import StageResult as SR
        from autocontext.harness.validation import StageStatus as SS
        from autocontext.harness.validation import ValidationPipeline as VP
        from autocontext.harness.validation import ValidationStage as VS

        assert SR is StageResult
        assert SS is StageStatus
        assert VP is ValidationPipeline
        assert VS is ValidationStage
