import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.harness_optimization.calibration import (
    cite_margin_vs_noise,
    compute_calibration,
    render_calibration_report,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "calibration-cases" / "calibration-cases.json"

_CASES: list[dict[str, Any]] = json.loads(FIX.read_text())["cases"]

_NUMERIC_FIELDS = (
    "mean",
    "variance",
    "std_dev",
    "standard_error",
    "recommended_min_delta",
)


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_calibration_cases_match_fixture(case: dict[str, Any]) -> None:
    report = compute_calibration(
        case["scores"],
        scenario_id=case["scenario_id"],
        current_min_delta=case["current_min_delta"],
        max_trials=case["max_trials"],
    )
    expected = case["expected"]

    assert report.sample_size == expected["sample_size"]
    assert report.recommended_trial_count == expected["recommended_trial_count"]
    assert report.margin_vs_noise == expected["margin_vs_noise"]
    assert report.sparse_metric_too_noisy is expected["sparse_metric_too_noisy"]

    for field in _NUMERIC_FIELDS:
        actual = getattr(report, field)
        assert abs(actual - expected[field]) <= 1e-9, field

    # When a case pins the exact rendered/citation text, both languages compare to
    # the same shared literal so any Python-vs-TS text drift fails a test.
    if "expected_rendered" in case:
        assert render_calibration_report(report) == case["expected_rendered"]
    if "expected_citation" in case:
        assert cite_margin_vs_noise(report) == case["expected_citation"]


def test_report_metadata_and_notes() -> None:
    report = compute_calibration(
        [0.80, 0.82, 0.79, 0.81, 0.80],
        scenario_id="grid_ctf",
        current_min_delta=0.05,
        max_trials=10,
    )
    assert report.schema_version == 1
    assert report.scenario_id == "grid_ctf"
    assert report.current_min_delta == 0.05
    assert report.notes == "SE=0.0051 over n=5; margin above_noise"


def _case_by_name(name: str) -> dict[str, Any]:
    for case in _CASES:
        if case["name"] == name:
            return case
    raise KeyError(name)


def _report_for(name: str) -> Any:
    case = _case_by_name(name)
    return compute_calibration(
        case["scores"],
        scenario_id=case["scenario_id"],
        current_min_delta=case["current_min_delta"],
        max_trials=case["max_trials"],
    )


def test_render_calibration_report_contains_fields() -> None:
    report = _report_for("low_noise")
    rendered = render_calibration_report(report)

    assert "calibration report:" in rendered
    assert report.scenario_id in rendered
    assert "standard_error:" in rendered
    assert "recommended_min_delta:" in rendered
    assert "recommended_trial_count:" in rendered
    assert "margin:" in rendered
    assert f"mean: {report.mean:.6f}" in rendered
    assert f"samples: {report.sample_size}" in rendered


def test_render_report_sparse_line_present_for_noisy() -> None:
    report = _report_for("high_noise")
    rendered = render_calibration_report(report)

    assert report.sparse_metric_too_noisy is True
    assert "sparse metric too noisy: optimize a denser verifier signal instead" in rendered
    assert rendered.splitlines()[-1] == ("sparse metric too noisy: optimize a denser verifier signal instead")


def test_render_report_sparse_line_absent_for_clean() -> None:
    report = _report_for("low_noise")
    rendered = render_calibration_report(report)

    assert report.sparse_metric_too_noisy is False
    assert "sparse metric too noisy" not in rendered


def test_cite_margin_vs_noise_format() -> None:
    report = _report_for("high_noise")
    citation = cite_margin_vs_noise(report)

    assert "margin" in citation
    assert report.margin_vs_noise in citation
    assert "recommended >=" in citation
    assert f"{report.current_min_delta:.6f}" in citation
    assert f"{report.recommended_min_delta:.6f}" in citation
    assert citation == (
        f"margin {report.current_min_delta:.6f} is {report.margin_vs_noise} (recommended >= {report.recommended_min_delta:.6f})"
    )


def test_under_sampled_series_is_insufficient_data_not_above_noise() -> None:
    # AC-881 review: with n < 2 there is no variance estimate, so a positive current_min_delta must
    # NOT be reported as "above_noise" (which would falsely reassure the operator that the margin
    # cleared noise). Both n=1 and n=0 report insufficient_data.
    one = compute_calibration([0.9], scenario_id="s", current_min_delta=0.05, max_trials=8)
    assert one.margin_vs_noise == "insufficient_data"
    assert one.standard_error == 0.0
    none = compute_calibration([], scenario_id="s", current_min_delta=0.05, max_trials=8)
    assert none.margin_vs_noise == "insufficient_data"
    # a well-sampled series still classifies normally
    many = compute_calibration([0.80, 0.81, 0.79, 0.80, 0.82], scenario_id="s", current_min_delta=0.5, max_trials=8)
    assert many.margin_vs_noise in ("above_noise", "below_noise")
