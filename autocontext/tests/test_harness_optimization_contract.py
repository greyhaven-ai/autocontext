"""Tests for the generated harness-optimization CandidateEvidence model (AC-876).

The model is generated from the canonical JSON Schema at
ts/src/harness-optimization/contract/json-schemas/candidate-evidence.schema.json
via ts/scripts/sync-python-harness-optimization-schemas.mjs. These tests pin the
runtime contract: required fields, extra=forbid, and enum validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.harness_optimization.contract.models import (
    CandidateEvidence,
    PromotionScore,
)

# Walk up to the repo root: autocontext/tests/ -> autocontext/ -> <repo root>.
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "harness-optimization" / "candidate-evidence"

# Both the Python and TypeScript packages load these exact same repo-root files.
EXPECTED_FIXTURES = {
    "valid-minimal.json",
    "valid-full.json",
    "invalid-missing-hypothesis.json",
    "invalid-bad-mechanism-type.json",
    "invalid-extra-field.json",
    "invalid-empty-parity.json",
}


def _fixtures(prefix: str) -> list[Path]:
    assert FIXTURES_DIR.is_dir(), f"expected fixtures dir at {FIXTURES_DIR}"
    return sorted(FIXTURES_DIR.glob(f"{prefix}*.json"))


def _valid_candidate() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "candidate_id": "cand-001",
        "mechanism_name": "tighten-validator-error-message",
        "mechanism_type": "deterministic_code",
        "target_surface": "harness_validator",
        "hypothesis": "Clearer validator errors reduce retry loops.",
        "changes": "Rewrite the validator failure string to name the missing field.",
        "validation_plan": "Replay the 5 known failing traces and confirm no regressions.",
        "parity": {
            "python": "implemented",
            "typescript": "pending",
            "schema_hash": "abc123",
        },
    }


def test_valid_candidate_round_trips() -> None:
    data = _valid_candidate()
    model = CandidateEvidence(**data)
    dumped = model.model_dump(exclude_none=True)
    for key, value in data.items():
        assert dumped[key] == value


def test_missing_required_field_raises() -> None:
    data = _valid_candidate()
    del data["hypothesis"]
    with pytest.raises(ValidationError):
        CandidateEvidence(**data)


def test_extra_field_raises() -> None:
    data = _valid_candidate()
    data["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        CandidateEvidence(**data)


def test_bad_mechanism_type_raises() -> None:
    data = _valid_candidate()
    data["mechanism_type"] = "not_a_real_mechanism"
    with pytest.raises(ValidationError):
        CandidateEvidence(**data)


@pytest.mark.parametrize("fixture", _fixtures("valid-"), ids=lambda p: p.name)
def test_shared_valid_fixtures_construct(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    CandidateEvidence(**data)


@pytest.mark.parametrize("fixture", _fixtures("invalid-"), ids=lambda p: p.name)
def test_shared_invalid_fixtures_raise(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    with pytest.raises(ValidationError):
        CandidateEvidence(**data)


def test_shared_fixture_directory_contains_the_expected_set() -> None:
    # Set-membership guard: a dropped or renamed fixture fails here.
    names = {p.name for p in FIXTURES_DIR.glob("*.json")}
    assert names == EXPECTED_FIXTURES


def _valid_promotion_score() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "candidate_id": "cand-001",
        "weight_version": "w-2026-07",
        "components": {
            "dense_quality_score": 0.82,
            "sparse_success_rate": 0.6,
            "tokens_per_million": 1200.0,
            "error_rate": 0.05,
            "score_variance": 0.01,
        },
        "weights": {
            "sparse_success_weight": 1.0,
            "token_cost_weight": 0.2,
            "error_weight": 0.5,
            "variance_weight": 0.1,
        },
        "score": 0.71,
        "parity": {
            "python": "implemented",
            "typescript": "pending",
            "schema_hash": "abc123",
        },
    }


def test_valid_promotion_score_round_trips() -> None:
    data = _valid_promotion_score()
    model = PromotionScore(**data)
    dumped = model.model_dump(exclude_none=True)
    assert dumped == data


def test_promotion_score_missing_required_component_raises() -> None:
    data = _valid_promotion_score()
    del data["components"]["error_rate"]
    with pytest.raises(ValidationError):
        PromotionScore(**data)


def test_promotion_score_extra_field_raises() -> None:
    data = _valid_promotion_score()
    data["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        PromotionScore(**data)


def test_promotion_score_empty_parity_raises() -> None:
    data = _valid_promotion_score()
    data["parity"] = {}
    with pytest.raises(ValidationError):
        PromotionScore(**data)
