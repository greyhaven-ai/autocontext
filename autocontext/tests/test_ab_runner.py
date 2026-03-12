"""Tests for A/B testing runner."""
from __future__ import annotations

from autocontext.evaluation.ab_runner import ABTestConfig, ABTestResult


def test_ab_config_fields() -> None:
    config = ABTestConfig(
        scenario="grid_ctf",
        baseline_env={"AUTOCONTEXT_RLM_ENABLED": "false"},
        treatment_env={"AUTOCONTEXT_RLM_ENABLED": "true"},
        runs_per_condition=3,
        generations_per_run=2,
    )
    assert config.scenario == "grid_ctf"
    assert config.runs_per_condition == 3


def test_ab_result_computes_delta() -> None:
    result = ABTestResult(
        baseline_scores=[0.3, 0.4, 0.35],
        treatment_scores=[0.5, 0.6, 0.55],
    )
    assert result.mean_delta() > 0
    assert result.treatment_wins() == 3
    assert result.baseline_wins() == 0


def test_ab_result_empty_returns_zero() -> None:
    result = ABTestResult()
    assert result.mean_delta() == 0.0
    assert result.treatment_wins() == 0
    assert result.baseline_wins() == 0


def test_ab_result_ties() -> None:
    result = ABTestResult(
        baseline_scores=[0.5, 0.5],
        treatment_scores=[0.5, 0.5],
    )
    assert result.mean_delta() == 0.0
    assert result.treatment_wins() == 0
    assert result.baseline_wins() == 0
