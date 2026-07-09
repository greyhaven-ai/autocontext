from __future__ import annotations

import json
from pathlib import Path

from autocontext.execution.evaluator_epoch import compute_evaluator_epoch

_FIXTURE = Path(__file__).parent / "fixtures" / "evaluator-epoch-cases.json"


def test_fixture_epoch_ids_match_reference() -> None:
    cases = json.loads(_FIXTURE.read_text())["cases"]
    assert len(cases) >= 4
    for case in cases:
        epoch = compute_evaluator_epoch(case["rubric_text"], case["judge_provider"], case["judge_model"])
        assert case["expected_epoch_id"], "fixture expected_epoch_id must be pinned (see plan Step 3)"
        assert epoch.epoch_id == case["expected_epoch_id"], case
