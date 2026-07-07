import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.harness_optimization.contract.models import (
    FrontierMechanism,
    OrphanMechanism,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "mechanism-archive"


def test_valid_frontier_round_trips() -> None:
    data = json.loads((FIX / "valid-frontier.json").read_text())
    model = FrontierMechanism.model_validate(data)
    assert model.mechanism_id == "frontier-ac880-001"
    assert model.promoted_at_generation == 7


def test_valid_orphan_round_trips() -> None:
    data = json.loads((FIX / "valid-orphan.json").read_text())
    model = OrphanMechanism.model_validate(data)
    assert model.failure_family == "regression-on-holdout"
    assert model.retry_count == 2


def test_orphan_missing_failure_family_rejected() -> None:
    data = json.loads((FIX / "invalid-orphan-missing-failure-family.json").read_text())
    with pytest.raises(ValidationError):
        OrphanMechanism.model_validate(data)
