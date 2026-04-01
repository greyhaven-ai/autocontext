from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# DisagreementMetrics dataclass tests
# ---------------------------------------------------------------------------

class TestDisagreementMetricsDefaults:
    """Test 1: DisagreementMetrics defaults are sensible."""

    def test_disagreement_metrics_defaults(self) -> None:
        from autocontext.execution.judge import DisagreementMetrics

        m = DisagreementMetrics()
        assert m.score_std_dev == 0.0
        assert m.score_range == (0.0, 0.0)
        assert m.sample_scores == []
        assert m.dimension_std_devs == {}
        assert m.is_high_disagreement is False
        assert m.sample_count == 1


class TestDisagreementMetricsFromSamples:
    """Test 2: DisagreementMetrics can be constructed from real sample data."""

    def test_disagreement_metrics_from_samples(self) -> None:
        from autocontext.execution.judge import DisagreementMetrics

        scores = [0.6, 0.8, 1.0]
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std_dev = math.sqrt(variance)

        m = DisagreementMetrics(
            score_std_dev=std_dev,
            score_range=(0.6, 1.0),
            sample_scores=scores,
            dimension_std_devs={"clarity": 0.1},
            is_high_disagreement=True,
            sample_count=3,
        )
        assert m.score_std_dev == pytest.approx(std_dev)
        assert m.score_range == (0.6, 1.0)
        assert m.sample_scores == [0.6, 0.8, 1.0]
        assert m.dimension_std_devs == {"clarity": 0.1}
        assert m.is_high_disagreement is True
        assert m.sample_count == 3


# ---------------------------------------------------------------------------
# JudgeResult with disagreement field
# ---------------------------------------------------------------------------

class TestJudgeResultDisagreementNoneSingleSample:
    """Test 3: disagreement is None when samples=1."""

    def test_judge_result_disagreement_none_single_sample(self) -> None:
        from autocontext.execution.judge import LLMJudge

        resp = (
            '<!-- JUDGE_RESULT_START -->'
            '{"score": 0.8, "reasoning": "ok", "dimensions": {"x": 0.7}}'
            '<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(model="test", rubric="R", llm_fn=lambda s, u: resp, samples=1)
        result = judge.evaluate("T", "O")
        assert result.disagreement is None


class TestJudgeResultDisagreementComputedMultiSample:
    """Test 4: disagreement is computed when samples>1."""

    def test_judge_result_disagreement_computed_multi_sample(self) -> None:
        from autocontext.execution.judge import DisagreementMetrics, LLMJudge

        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "R1", "dimensions": {"x": 0.6}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.6, "reasoning": "R2", "dimensions": {"x": 0.4}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=2)
        result = judge.evaluate("T", "O")
        assert result.disagreement is not None
        assert isinstance(result.disagreement, DisagreementMetrics)
        assert result.disagreement.sample_count == 2
        assert result.disagreement.sample_scores == [0.8, 0.6]


class TestJudgeDisagreementStdDevCorrect:
    """Test 5: std dev calculation is mathematically correct."""

    def test_judge_disagreement_std_dev_correct(self) -> None:
        from autocontext.execution.judge import LLMJudge

        # Scores: 0.8, 0.6. Mean=0.7. Variance=0.01. StdDev=0.1
        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "R1", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.6, "reasoning": "R2", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=2)
        result = judge.evaluate("T", "O")

        assert result.disagreement is not None
        # Population std dev: sqrt(((0.8-0.7)^2 + (0.6-0.7)^2) / 2) = sqrt(0.01) = 0.1
        assert result.disagreement.score_std_dev == pytest.approx(0.1)


class TestJudgeDisagreementRangeCorrect:
    """Test 6: score_range min/max correct."""

    def test_judge_disagreement_range_correct(self) -> None:
        from autocontext.execution.judge import LLMJudge

        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.3, "reasoning": "R1", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.9, "reasoning": "R2", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.5, "reasoning": "R3", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=3)
        result = judge.evaluate("T", "O")

        assert result.disagreement is not None
        assert result.disagreement.score_range == (0.3, 0.9)


class TestJudgeDisagreementHighFlag:
    """Test 7: is_high_disagreement=True when std_dev > threshold."""

    def test_judge_disagreement_high_flag(self) -> None:
        from autocontext.execution.judge import LLMJudge

        # Scores 0.1 and 0.9: mean=0.5, std_dev=0.4. Default threshold=0.15.
        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.1, "reasoning": "R1", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.9, "reasoning": "R2", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=2)
        result = judge.evaluate("T", "O")

        assert result.disagreement is not None
        assert result.disagreement.is_high_disagreement is True


class TestJudgeDisagreementLowFlag:
    """Test 8: is_high_disagreement=False when std_dev < threshold."""

    def test_judge_disagreement_low_flag(self) -> None:
        from autocontext.execution.judge import LLMJudge

        # Scores 0.80 and 0.82: mean=0.81, std_dev=0.01. Default threshold=0.15.
        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.80, "reasoning": "R1", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.82, "reasoning": "R2", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=2)
        result = judge.evaluate("T", "O")

        assert result.disagreement is not None
        assert result.disagreement.is_high_disagreement is False


class TestJudgeDisagreementDimensionStdDevs:
    """Test 9: per-dimension std devs computed correctly."""

    def test_judge_disagreement_dimension_std_devs(self) -> None:
        from autocontext.execution.judge import LLMJudge

        # dimension "x": [0.6, 0.4] -> mean=0.5, std_dev=0.1
        # dimension "y": [0.9, 0.9] -> mean=0.9, std_dev=0.0
        responses = [
            '<!-- JUDGE_RESULT_START -->'
            '{"score": 0.8, "reasoning": "R1", "dimensions": {"x": 0.6, "y": 0.9}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->'
            '{"score": 0.6, "reasoning": "R2", "dimensions": {"x": 0.4, "y": 0.9}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=2)
        result = judge.evaluate("T", "O")

        assert result.disagreement is not None
        assert result.disagreement.dimension_std_devs["x"] == pytest.approx(0.1)
        assert result.disagreement.dimension_std_devs["y"] == pytest.approx(0.0)


class TestJudgeDisagreementCustomThreshold:
    """Test 10: custom disagreement_threshold is respected."""

    def test_judge_disagreement_custom_threshold(self) -> None:
        from autocontext.execution.judge import LLMJudge

        # Scores 0.8 and 0.6: std_dev=0.1. With threshold=0.05, should be high.
        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "R1", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.6, "reasoning": "R2", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(
            model="test", rubric="R", llm_fn=multi_llm, samples=2,
            disagreement_threshold=0.05,
        )
        result = judge.evaluate("T", "O")

        assert result.disagreement is not None
        assert result.disagreement.is_high_disagreement is True  # 0.1 > 0.05

    def test_judge_disagreement_custom_threshold_not_exceeded(self) -> None:
        from autocontext.execution.judge import LLMJudge

        # Scores 0.8 and 0.6: std_dev=0.1. With threshold=0.5, should NOT be high.
        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "R1", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.6, "reasoning": "R2", "dimensions": {}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(
            model="test", rubric="R", llm_fn=multi_llm, samples=2,
            disagreement_threshold=0.5,
        )
        result = judge.evaluate("T", "O")

        assert result.disagreement is not None
        assert result.disagreement.is_high_disagreement is False  # 0.1 < 0.5


# ---------------------------------------------------------------------------
# Bias probe tests
# ---------------------------------------------------------------------------

class TestPositionBiasProbeNoBias:
    """Test 11: equal scores in both orderings -> no bias detected."""

    def test_position_bias_probe_no_bias(self) -> None:
        from autocontext.execution.bias_probes import BiasProbeResult, run_position_bias_probe
        from autocontext.providers.callable_wrapper import CallableProvider

        # For no position bias: score_ab should roughly equal 1 - score_ba
        # i.e., A-first score for A = 0.8, B-first score for B = 0.2 (meaning A still gets 0.8)
        call_idx = 0

        def fair_llm(system: str, user: str) -> str:
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                # A-first: score Candidate 1 (A) = 0.8
                return (
                    '<!-- JUDGE_RESULT_START -->'
                    '{"score": 0.8, "reasoning": "A is good"}'
                    '<!-- JUDGE_RESULT_END -->'
                )
            else:
                # B-first: score Candidate 1 (B) = 0.2 (A would be 0.8)
                return (
                    '<!-- JUDGE_RESULT_START -->'
                    '{"score": 0.2, "reasoning": "B is ok"}'
                    '<!-- JUDGE_RESULT_END -->'
                )

        provider = CallableProvider(fair_llm, model_name="test")
        result = run_position_bias_probe(
            provider=provider,
            model="test",
            system_prompt="Judge",
            candidate_a="Output A",
            candidate_b="Output B",
            rubric="Be good",
        )
        assert isinstance(result, BiasProbeResult)
        assert result.probe_type == "position"
        assert result.detected is False


class TestPositionBiasProbeDetected:
    """Test 12: systematically different scores -> bias detected."""

    def test_position_bias_probe_detected(self) -> None:
        from autocontext.execution.bias_probes import run_position_bias_probe
        from autocontext.providers.callable_wrapper import CallableProvider

        # Position bias: judge always gives high score to Candidate 1
        call_idx = 0

        def biased_llm(system: str, user: str) -> str:
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                # A-first: score Candidate 1 (A) = 0.9
                return (
                    '<!-- JUDGE_RESULT_START -->'
                    '{"score": 0.9, "reasoning": "first is great"}'
                    '<!-- JUDGE_RESULT_END -->'
                )
            else:
                # B-first: score Candidate 1 (B) = 0.9 (should be ~0.1 if fair)
                return (
                    '<!-- JUDGE_RESULT_START -->'
                    '{"score": 0.9, "reasoning": "first is great"}'
                    '<!-- JUDGE_RESULT_END -->'
                )

        provider = CallableProvider(biased_llm, model_name="test")
        result = run_position_bias_probe(
            provider=provider,
            model="test",
            system_prompt="Judge",
            candidate_a="Output A",
            candidate_b="Output B",
            rubric="Be good",
        )
        assert result.probe_type == "position"
        assert result.detected is True
        assert result.magnitude > 0.1


class TestBiasReportAggregation:
    """Test 13: BiasReport correctly aggregates multiple probe results."""

    def test_bias_report_aggregation(self) -> None:
        from autocontext.execution.bias_probes import BiasProbeResult, BiasReport

        r1 = BiasProbeResult(probe_type="position", detected=False, magnitude=0.05, details="ok")
        r2 = BiasProbeResult(probe_type="style", detected=True, magnitude=0.3, details="style bias")

        report = BiasReport(
            probes_run=2,
            probes_failed=0,
            results=[r1, r2],
            any_bias_detected=True,
        )
        assert report.probes_run == 2
        assert report.probes_failed == 0
        assert len(report.results) == 2
        assert report.any_bias_detected is True


class TestBiasReportTypesDetected:
    """Test 14: bias_types_detected property works."""

    def test_bias_report_types_detected(self) -> None:
        from autocontext.execution.bias_probes import BiasProbeResult, BiasReport

        r1 = BiasProbeResult(probe_type="position", detected=True, magnitude=0.2)
        r2 = BiasProbeResult(probe_type="style", detected=False, magnitude=0.01)
        r3 = BiasProbeResult(probe_type="length", detected=True, magnitude=0.15)

        report = BiasReport(
            probes_run=3,
            probes_failed=0,
            results=[r1, r2, r3],
            any_bias_detected=True,
        )
        types = report.bias_types_detected
        assert "position" in types
        assert "length" in types
        assert "style" not in types

    def test_bias_report_to_dict_includes_detected_types(self) -> None:
        from autocontext.execution.bias_probes import BiasProbeResult, BiasReport

        report = BiasReport(
            probes_run=2,
            probes_failed=0,
            results=[
                BiasProbeResult(probe_type="position", detected=True, magnitude=0.2),
                BiasProbeResult(probe_type="style", detected=False, magnitude=0.01),
            ],
            any_bias_detected=True,
        )

        payload = report.to_dict()
        assert payload["bias_types_detected"] == ["position"]


class TestJudgeResultDefaults:
    def test_defaults_are_real_containers(self) -> None:
        from autocontext.execution.judge import JudgeResult

        result = JudgeResult(score=0.8, reasoning="ok")

        assert result.dimension_scores == {}
        assert result.raw_responses == []


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------

class TestDisagreementSettingsDefaults:
    """Test 15: judge_disagreement_threshold=0.15, bias_probes_enabled=False."""

    def test_disagreement_settings_defaults(self) -> None:
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        assert settings.judge_disagreement_threshold == 0.15
        assert settings.judge_bias_probes_enabled is False


class TestDisagreementSettingsFromEnv:
    """Test 16: AUTOCONTEXT_JUDGE_DISAGREEMENT_THRESHOLD loads correctly."""

    def test_disagreement_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_JUDGE_DISAGREEMENT_THRESHOLD", "0.25")
        monkeypatch.setenv("AUTOCONTEXT_JUDGE_BIAS_PROBES_ENABLED", "true")
        settings = load_settings()
        assert settings.judge_disagreement_threshold == pytest.approx(0.25)
        assert settings.judge_bias_probes_enabled is True


# ---------------------------------------------------------------------------
# Integration: existing multi-sample still works with new field
# ---------------------------------------------------------------------------

class TestExistingMultiSampleCompatibility:
    """Test 17: existing multi-sample test still works with disagreement."""

    def test_existing_multi_sample_with_disagreement(self) -> None:
        from autocontext.execution.judge import LLMJudge

        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "R1", "dimensions": {"x": 0.6}}'
            '<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.6, "reasoning": "R2", "dimensions": {"x": 0.4}}'
            '<!-- JUDGE_RESULT_END -->',
        ]
        idx = 0

        def multi_llm(s: str, u: str) -> str:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=2)
        result = judge.evaluate("T", "O")

        # Original assertions still hold
        assert abs(result.score - 0.7) < 1e-9
        assert abs(result.dimension_scores["x"] - 0.5) < 1e-9
        assert "R1" in result.reasoning
        assert "R2" in result.reasoning
        assert len(result.raw_responses) == 2

        # New: disagreement is computed
        assert result.disagreement is not None
        assert result.disagreement.sample_count == 2


class TestSingleSampleReturnsNoneDisagreement:
    """Test 18: single-sample evaluation returns disagreement=None."""

    def test_single_sample_returns_none_disagreement(self) -> None:
        from autocontext.execution.judge import LLMJudge

        resp = (
            '<!-- JUDGE_RESULT_START -->'
            '{"score": 0.85, "reasoning": "Good", "dimensions": {"clarity": 0.9}}'
            '<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(model="test", rubric="Be good", llm_fn=lambda s, u: resp)
        result = judge.evaluate("Do task", "My output")
        assert result.disagreement is None
