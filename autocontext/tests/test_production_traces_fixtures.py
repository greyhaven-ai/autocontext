"""Fixture-driven parity tests for production_traces Pydantic validation.

Reads the canonical cross-runtime fixtures from the TS tests directory (same
files the TS AJV side exercises) and asserts the Python validator agrees on
acceptance / rejection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.production_traces import validate_production_trace

# Walk up to the worktree root: autocontext/tests/ -> autocontext/ -> <worktree>
WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = WORKTREE_ROOT / "ts" / "tests" / "control-plane" / "production-traces" / "fixtures"


def _all_fixtures() -> list[Path]:
    assert FIXTURES_DIR.is_dir(), f"expected fixtures dir at {FIXTURES_DIR}"
    return sorted(FIXTURES_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture", [p for p in _all_fixtures() if p.name.startswith("valid-")], ids=lambda p: p.name)
def test_valid_fixtures_accepted(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    trace = validate_production_trace(data)
    # Sanity: schemaVersion populated from input.
    assert trace.schemaVersion == "1.0"


@pytest.mark.parametrize("fixture", [p for p in _all_fixtures() if p.name.startswith("invalid-")], ids=lambda p: p.name)
def test_invalid_fixtures_rejected(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_fixture_directory_contains_the_expected_set() -> None:
    # Sanity check — catches accidentally dropped fixtures.
    names = {p.name for p in _all_fixtures()}
    required = {
        "valid-minimal.json",
        "valid-openai.json",
        "valid-anthropic.json",
        "valid-tool-calls.json",
        "valid-with-outcome.json",
        "valid-with-feedback.json",
        "valid-with-redaction-markers.json",
        "invalid-missing-required.json",
        "invalid-bad-timing.json",
    }
    missing = required - names
    assert not missing, f"missing fixtures: {sorted(missing)}"
