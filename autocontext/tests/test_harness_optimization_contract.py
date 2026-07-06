"""Tests for the generated harness-optimization CandidateEvidence model (AC-876).

The model is generated from the canonical JSON Schema at
ts/src/harness-optimization/contract/json-schemas/candidate-evidence.schema.json
via ts/scripts/sync-python-harness-optimization-schemas.mjs. These tests pin the
runtime contract: required fields, extra=forbid, and enum validation.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.harness_optimization.contract.models import CandidateEvidence


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
