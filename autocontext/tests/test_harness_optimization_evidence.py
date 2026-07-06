"""Tests for the CandidateEvidence read/write/validate API (AC-876)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.harness_optimization.evidence import (
    CandidateEvidence,
    read_candidate_evidence,
    validate_candidate_evidence,
    write_candidate_evidence,
)

# Walk up to the repo root: autocontext/tests/ -> autocontext/ -> <repo root>.
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "harness-optimization" / "candidate-evidence"
VALID_FULL = FIXTURES_DIR / "valid-full.json"
INVALID_MISSING = FIXTURES_DIR / "invalid-missing-hypothesis.json"


def test_read_write_read_round_trips(tmp_path: Path) -> None:
    original = read_candidate_evidence(VALID_FULL)
    out = tmp_path / "nested" / "candidate.json"
    write_candidate_evidence(original, out)
    reloaded = read_candidate_evidence(out)
    assert reloaded == original


def test_write_creates_parent_dirs_and_trailing_newline(tmp_path: Path) -> None:
    evidence = read_candidate_evidence(VALID_FULL)
    out = tmp_path / "a" / "b" / "c" / "candidate.json"
    write_candidate_evidence(evidence, out)
    assert out.exists()
    assert out.read_text().endswith("\n")


def test_write_then_read_preserves_all_fields(tmp_path: Path) -> None:
    evidence = read_candidate_evidence(VALID_FULL)
    out = tmp_path / "candidate.json"
    write_candidate_evidence(evidence, out)
    on_disk = json.loads(out.read_text())
    expected = evidence.model_dump(mode="json", exclude_none=False)
    assert on_disk == expected


def test_validate_candidate_evidence_accepts_valid_dict() -> None:
    data = json.loads(VALID_FULL.read_text())
    model = validate_candidate_evidence(data)
    assert isinstance(model, CandidateEvidence)
    assert model.candidate_id == data["candidate_id"]


def test_validate_candidate_evidence_raises_on_invalid_fixture() -> None:
    data = json.loads(INVALID_MISSING.read_text())
    with pytest.raises(ValidationError):
        validate_candidate_evidence(data)


def test_read_candidate_evidence_raises_on_invalid_fixture() -> None:
    with pytest.raises(ValidationError):
        read_candidate_evidence(INVALID_MISSING)
