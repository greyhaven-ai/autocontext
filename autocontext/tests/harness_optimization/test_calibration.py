import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.harness_optimization.calibration import compute_calibration

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
