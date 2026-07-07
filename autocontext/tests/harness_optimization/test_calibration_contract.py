import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.harness_optimization.contract.models import CalibrationReport

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "calibration-report"


def test_valid_report_round_trips() -> None:
    data = json.loads((FIX / "valid-report.json").read_text())
    model = CalibrationReport.model_validate(data)
    assert model.scenario_id == "grid_ctf"
    assert model.margin_vs_noise == "above_noise"
    assert model.sparse_metric_too_noisy is False


def test_missing_standard_error_rejected() -> None:
    data = json.loads((FIX / "invalid-missing-standard-error.json").read_text())
    with pytest.raises(ValidationError):
        CalibrationReport.model_validate(data)
