import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.harness_optimization.contract.models import IntegrityMetadata

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "integrity-metadata"


def test_valid_verified_clean_round_trips() -> None:
    data = json.loads((FIX / "valid-verified-clean.json").read_text())
    model = IntegrityMetadata.model_validate(data)
    assert model.mode == "verified"
    assert model.leakage_status == "clean"


def test_missing_mode_rejected() -> None:
    data = json.loads((FIX / "invalid-missing-mode.json").read_text())
    with pytest.raises(ValidationError):
        IntegrityMetadata.model_validate(data)
