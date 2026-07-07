"""Parity tests for the harness promotion scorer (AC-877).

Loads the shared repo-root fixture and asserts the Python scorer matches every
hand-computed ground-truth value. The TypeScript suite
(``ts/tests/harness-optimization/scoring.test.ts``) loads the SAME fixture, which
is what proves the two implementations compute identical scores.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.harness_optimization.scoring import (
    beats_incumbent,
    components_from_dict,
    harness_promotion_score,
    weights_from_dict,
)

# Walk up to the repo root: autocontext/tests/ -> autocontext/ -> <repo root>.
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "harness-optimization" / "promotion-score" / "score-cases.json"

_CASES = json.loads(FIXTURE.read_text())
SCORE_CASES = _CASES["score_cases"]
BEATS_CASES = _CASES["beats_cases"]

TOL = 1e-9


@pytest.mark.parametrize("case", SCORE_CASES, ids=[c["name"] for c in SCORE_CASES])
def test_harness_promotion_score_matches_fixture(case: dict) -> None:
    components = components_from_dict(case["components"])
    weights = weights_from_dict(case["weights"])
    assert harness_promotion_score(components, weights) == pytest.approx(case["expected_score"], abs=TOL)


@pytest.mark.parametrize("case", BEATS_CASES, ids=[c["name"] for c in BEATS_CASES])
def test_beats_incumbent_matches_fixture(case: dict) -> None:
    challenger = components_from_dict(case["challenger"])
    incumbent = components_from_dict(case["incumbent"])
    weights = weights_from_dict(case["weights"])
    assert beats_incumbent(challenger, incumbent, weights, case["min_margin"]) == case["expected_beats"]
