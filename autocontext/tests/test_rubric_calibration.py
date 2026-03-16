"""Tests for AC-283: rubric calibration — human anchors, judge variance, alignment.

Covers: CalibrationAnchor, CalibrationSet, measure_judge_variance,
compute_alignment, AlignmentTolerance, CalibrationReport.
"""

from __future__ import annotations

import pytest

from autocontext.providers.base import CompletionResult, LLMProvider


class _MockJudgeProvider(LLMProvider):
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
        return CompletionResult(text=self._response, model=model or "mock-judge")

    def default_model(self):
        return "mock-judge"


def _judge_response(score: float, reasoning: str = "aligned") -> str:
    import json

    payload = {
        "score": score,
        "reasoning": reasoning,
        "dimensions": {"quality": score},
    }
    return f"<!-- JUDGE_RESULT_START -->\n{json.dumps(payload)}\n<!-- JUDGE_RESULT_END -->"

# ===========================================================================
# CalibrationAnchor
# ===========================================================================


class TestCalibrationAnchor:
    def test_construction(self) -> None:
        from autocontext.execution.rubric_calibration import CalibrationAnchor

        anchor = CalibrationAnchor(
            anchor_id="a-1",
            domain="clinical_trial",
            output_text="A detailed clinical trial protocol...",
            human_score=0.82,
            score_band="good",
            human_notes="Thorough but missing secondary endpoints",
        )
        assert anchor.human_score == 0.82
        assert anchor.score_band == "good"

    def test_roundtrip(self) -> None:
        from autocontext.execution.rubric_calibration import CalibrationAnchor

        anchor = CalibrationAnchor(
            anchor_id="a-2",
            domain="drug_interaction",
            output_text="Output text",
            human_score=0.45,
            score_band="poor",
            human_notes="Missed critical interactions",
        )
        d = anchor.to_dict()
        restored = CalibrationAnchor.from_dict(d)
        assert restored.anchor_id == "a-2"
        assert restored.human_score == 0.45


# ===========================================================================
# CalibrationSet
# ===========================================================================


class TestCalibrationSet:
    def test_construction(self) -> None:
        from autocontext.execution.rubric_calibration import (
            CalibrationAnchor,
            CalibrationSet,
        )

        anchors = [
            CalibrationAnchor("a-1", "clinical_trial", "Poor output", 0.30, "poor", "Very weak"),
            CalibrationAnchor("a-2", "clinical_trial", "OK output", 0.60, "fair", "Adequate"),
            CalibrationAnchor("a-3", "clinical_trial", "Good output", 0.85, "good", "Solid work"),
        ]
        cal_set = CalibrationSet(domain="clinical_trial", anchors=anchors)
        assert cal_set.domain == "clinical_trial"
        assert len(cal_set.anchors) == 3

    def test_score_bands(self) -> None:
        from autocontext.execution.rubric_calibration import (
            CalibrationAnchor,
            CalibrationSet,
        )

        anchors = [
            CalibrationAnchor("a-1", "test", "output", 0.30, "poor", ""),
            CalibrationAnchor("a-2", "test", "output", 0.60, "fair", ""),
            CalibrationAnchor("a-3", "test", "output", 0.85, "good", ""),
            CalibrationAnchor("a-4", "test", "output", 0.95, "excellent", ""),
        ]
        cal_set = CalibrationSet(domain="test", anchors=anchors)
        bands = cal_set.score_bands()
        assert "poor" in bands
        assert "excellent" in bands

    def test_roundtrip(self) -> None:
        from autocontext.execution.rubric_calibration import (
            CalibrationAnchor,
            CalibrationSet,
        )

        cal_set = CalibrationSet(
            domain="test",
            anchors=[CalibrationAnchor("a-1", "test", "out", 0.5, "fair", "ok")],
        )
        d = cal_set.to_dict()
        restored = CalibrationSet.from_dict(d)
        assert restored.domain == "test"
        assert len(restored.anchors) == 1


# ===========================================================================
# measure_judge_variance
# ===========================================================================


class TestMeasureJudgeVariance:
    def test_identical_scores_zero_variance(self) -> None:
        from autocontext.execution.rubric_calibration import measure_judge_variance

        result = measure_judge_variance([0.75, 0.75, 0.75, 0.75, 0.75])
        assert result.variance == 0.0
        assert result.std_dev == 0.0
        assert result.range == 0.0

    def test_varying_scores(self) -> None:
        from autocontext.execution.rubric_calibration import measure_judge_variance

        result = measure_judge_variance([0.70, 0.75, 0.80, 0.72, 0.78])
        assert result.variance > 0
        assert result.std_dev > 0
        assert result.mean > 0.7
        assert result.range == 0.10

    def test_single_score(self) -> None:
        from autocontext.execution.rubric_calibration import measure_judge_variance

        result = measure_judge_variance([0.80])
        assert result.variance == 0.0
        assert result.mean == 0.80

    def test_empty_scores(self) -> None:
        from autocontext.execution.rubric_calibration import measure_judge_variance

        result = measure_judge_variance([])
        assert result.mean == 0.0
        assert result.num_samples == 0


# ===========================================================================
# compute_alignment
# ===========================================================================


class TestComputeAlignment:
    def test_perfect_alignment(self) -> None:
        from autocontext.execution.rubric_calibration import compute_alignment

        human_scores = [0.3, 0.5, 0.7, 0.9]
        judge_scores = [0.3, 0.5, 0.7, 0.9]
        result = compute_alignment(human_scores, judge_scores)
        assert result.mean_absolute_error == 0.0
        assert result.bias == 0.0
        assert result.correlation >= 0.99

    def test_systematic_overestimation(self) -> None:
        from autocontext.execution.rubric_calibration import compute_alignment

        human_scores = [0.3, 0.5, 0.7]
        judge_scores = [0.5, 0.7, 0.9]
        result = compute_alignment(human_scores, judge_scores)
        assert result.bias > 0  # judge scores higher than human
        assert result.mean_absolute_error > 0

    def test_high_correlation_with_offset(self) -> None:
        from autocontext.execution.rubric_calibration import compute_alignment

        human_scores = [0.2, 0.4, 0.6, 0.8]
        judge_scores = [0.3, 0.5, 0.7, 0.9]  # +0.1 systematic offset
        result = compute_alignment(human_scores, judge_scores)
        assert result.correlation >= 0.99  # Perfect correlation despite offset
        assert abs(result.bias - 0.1) < 0.01  # Bias is ~0.1

    def test_no_correlation(self) -> None:
        from autocontext.execution.rubric_calibration import compute_alignment

        human_scores = [0.2, 0.8, 0.4, 0.6]
        judge_scores = [0.7, 0.3, 0.9, 0.1]
        result = compute_alignment(human_scores, judge_scores)
        assert result.correlation < 0.5

    def test_empty_scores(self) -> None:
        from autocontext.execution.rubric_calibration import compute_alignment

        result = compute_alignment([], [])
        assert result.mean_absolute_error == 0.0
        assert result.num_pairs == 0

    def test_rejects_mismatched_anchor_sets(self) -> None:
        from autocontext.execution.rubric_calibration import compute_alignment

        with pytest.raises(ValueError, match="same length"):
            compute_alignment([0.4, 0.7, 0.9], [0.4, 0.7])


# ===========================================================================
# AlignmentTolerance
# ===========================================================================


class TestAlignmentTolerance:
    def test_construction(self) -> None:
        from autocontext.execution.rubric_calibration import AlignmentTolerance

        tol = AlignmentTolerance(
            domain="clinical_trial",
            max_mean_absolute_error=0.15,
            max_bias=0.10,
            min_correlation=0.80,
        )
        assert tol.max_mean_absolute_error == 0.15

    def test_check_passes(self) -> None:
        from autocontext.execution.rubric_calibration import (
            AlignmentResult,
            AlignmentTolerance,
        )

        tol = AlignmentTolerance("test", 0.15, 0.10, 0.80)
        alignment = AlignmentResult(
            mean_absolute_error=0.08,
            bias=0.05,
            correlation=0.92,
            num_pairs=5,
            per_anchor_errors=[0.05, 0.10, 0.08, 0.12, 0.05],
        )
        check = tol.check(alignment)
        assert check["passes"] is True

    def test_check_fails_on_mae(self) -> None:
        from autocontext.execution.rubric_calibration import (
            AlignmentResult,
            AlignmentTolerance,
        )

        tol = AlignmentTolerance("test", 0.10, 0.10, 0.80)
        alignment = AlignmentResult(
            mean_absolute_error=0.20,
            bias=0.05,
            correlation=0.90,
            num_pairs=3,
            per_anchor_errors=[0.15, 0.25, 0.20],
        )
        check = tol.check(alignment)
        assert check["passes"] is False
        assert "mean_absolute_error" in str(check["violations"])


# ===========================================================================
# CalibrationReport
# ===========================================================================


class TestCalibrationReport:
    def test_construction(self) -> None:
        from autocontext.execution.rubric_calibration import (
            AlignmentResult,
            CalibrationReport,
            JudgeVarianceResult,
        )

        report = CalibrationReport(
            domain="clinical_trial",
            num_anchors=3,
            alignment=AlignmentResult(
                mean_absolute_error=0.10,
                bias=0.05,
                correlation=0.90,
                num_pairs=3,
                per_anchor_errors=[0.08, 0.12, 0.10],
            ),
            variance=JudgeVarianceResult(
                mean=0.75,
                variance=0.001,
                std_dev=0.032,
                range=0.08,
                num_samples=5,
            ),
            calibrated=True,
        )
        assert report.calibrated is True
        assert report.num_anchors == 3

    def test_summary(self) -> None:
        from autocontext.execution.rubric_calibration import (
            AlignmentResult,
            CalibrationReport,
            JudgeVarianceResult,
        )

        report = CalibrationReport(
            domain="drug_interaction",
            num_anchors=4,
            alignment=AlignmentResult(0.12, 0.08, 0.88, 4, [0.10, 0.15, 0.08, 0.15]),
            variance=JudgeVarianceResult(0.72, 0.002, 0.045, 0.10, 5),
            calibrated=False,
        )
        summary = report.summary()
        assert "drug_interaction" in summary
        assert "0.12" in summary or "mae" in summary.lower()

    def test_roundtrip(self) -> None:
        from autocontext.execution.rubric_calibration import (
            AlignmentResult,
            CalibrationReport,
            JudgeVarianceResult,
        )

        report = CalibrationReport(
            domain="test",
            num_anchors=2,
            alignment=AlignmentResult(0.1, 0.05, 0.9, 2, [0.1, 0.1]),
            variance=JudgeVarianceResult(0.7, 0.001, 0.03, 0.05, 3),
            calibrated=True,
        )
        d = report.to_dict()
        restored = CalibrationReport.from_dict(d)
        assert restored.domain == "test"
        assert restored.calibrated is True


class TestRunJudgeCalibration:
    def test_builds_live_report_from_human_feedback_examples(self) -> None:
        from autocontext.execution.rubric_calibration import run_judge_calibration

        provider = _MockJudgeProvider(_judge_response(0.82))
        report = run_judge_calibration(
            domain="l19-drug-interactions",
            task_prompt="Find clinically relevant drug interactions.",
            rubric="Clinical accuracy and recall",
            provider=provider,
            model="mock-judge",
            calibration_examples=[
                {
                    "id": 1,
                    "agent_output": "Warfarin and aspirin cause bleeding risk.",
                    "human_score": 0.8,
                    "human_notes": "Correct high-severity interaction.",
                    "created_at": "2026-03-16T00:00:00Z",
                },
                {
                    "id": 2,
                    "agent_output": "Simvastatin and clarithromycin raise myopathy risk.",
                    "human_score": 0.84,
                    "human_notes": "Also correct and clinically relevant.",
                    "created_at": "2026-03-16T00:01:00Z",
                },
            ],
        )

        assert report is not None
        assert report.num_anchors == 2
        assert report.alignment.num_pairs == 2
        assert "per_anchor_variance" in report.metadata
