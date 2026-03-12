"""Tests for A/B statistical analysis."""
from __future__ import annotations

from autocontext.evaluation.ab_stats import mcnemar_test


def test_significant_improvement() -> None:
    # 10 fail->pass, 1 pass->fail => significant (n=11 discordant, p≈0.012)
    baseline = [False] * 10 + [True] * 1
    treatment = [True] * 10 + [False] * 1
    report = mcnemar_test(baseline_passed=baseline, treatment_passed=treatment)
    assert report.p_value < 0.05
    assert report.fail_to_pass > report.pass_to_fail
    assert report.significant


def test_no_difference() -> None:
    passed = [True, False, True, False, True]
    report = mcnemar_test(baseline_passed=passed, treatment_passed=passed)
    assert report.fail_to_pass == 0
    assert report.pass_to_fail == 0
    assert report.p_value == 1.0
    assert not report.significant


def test_report_markdown() -> None:
    report = mcnemar_test(
        baseline_passed=[False, False, True],
        treatment_passed=[True, True, True],
    )
    md = report.to_markdown()
    assert "McNemar" in md
    assert "p-value" in md.lower() or "p_value" in md.lower()


def test_mismatched_lengths_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="same length"):
        mcnemar_test(baseline_passed=[True], treatment_passed=[True, False])


def test_all_concordant() -> None:
    report = mcnemar_test(
        baseline_passed=[True, True, False],
        treatment_passed=[True, True, False],
    )
    assert report.both_pass == 2
    assert report.both_fail == 1
    assert report.fail_to_pass == 0
    assert report.pass_to_fail == 0
