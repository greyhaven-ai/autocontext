"""Fixture-driven parity tests for browser contract validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.integrations.browser.validate import (
    validate_browser_action,
    validate_browser_action_dict,
    validate_browser_audit_event,
    validate_browser_audit_event_dict,
    validate_browser_session_config,
    validate_browser_session_config_dict,
    validate_browser_snapshot,
    validate_browser_snapshot_dict,
)

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = WORKTREE_ROOT / "ts" / "tests" / "integrations" / "browser" / "fixtures"


def _all_fixtures() -> list[Path]:
    assert FIXTURES_DIR.is_dir(), f"expected fixtures dir at {FIXTURES_DIR}"
    return sorted(FIXTURES_DIR.glob("*.json"))


def _validator_for_fixture_name(name: str):
    if "-session-config-" in name:
        return validate_browser_session_config
    if "-action-" in name:
        return validate_browser_action
    if "-snapshot-" in name:
        return validate_browser_snapshot
    if "-audit-event-" in name:
        return validate_browser_audit_event
    raise AssertionError(f"unrecognized fixture name: {name}")


def _dict_validator_for_fixture_name(name: str):
    if "-session-config-" in name:
        return validate_browser_session_config_dict
    if "-action-" in name:
        return validate_browser_action_dict
    if "-snapshot-" in name:
        return validate_browser_snapshot_dict
    if "-audit-event-" in name:
        return validate_browser_audit_event_dict
    raise AssertionError(f"unrecognized fixture name: {name}")


@pytest.mark.parametrize("fixture", [p for p in _all_fixtures() if p.name.startswith("valid-")], ids=lambda p: p.name)
def test_valid_browser_fixtures_accepted(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    validator = _validator_for_fixture_name(fixture.name)
    doc = validator(data)
    assert doc.schemaVersion == "1.0"


@pytest.mark.parametrize("fixture", [p for p in _all_fixtures() if p.name.startswith("valid-")], ids=lambda p: p.name)
def test_valid_browser_fixtures_accepted_by_dict_helpers(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    validator = _dict_validator_for_fixture_name(fixture.name)
    valid, errors = validator(data)
    assert valid
    assert not errors


@pytest.mark.parametrize("fixture", [p for p in _all_fixtures() if p.name.startswith("invalid-")], ids=lambda p: p.name)
def test_invalid_browser_fixtures_rejected(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    validator = _validator_for_fixture_name(fixture.name)
    with pytest.raises(ValidationError):
        validator(data)


@pytest.mark.parametrize("fixture", [p for p in _all_fixtures() if p.name.startswith("invalid-")], ids=lambda p: p.name)
def test_invalid_browser_fixtures_rejected_by_dict_helpers(fixture: Path) -> None:
    data = json.loads(fixture.read_text())
    validator = _dict_validator_for_fixture_name(fixture.name)
    valid, errors = validator(data)
    assert not valid
    assert errors


def test_browser_fixture_directory_contains_expected_set() -> None:
    names = {p.name for p in _all_fixtures()}
    required = {
        "invalid-action-fill-null-field-kind.json",
        "valid-session-config-ephemeral.json",
        "valid-session-config-isolated-downloads.json",
        "invalid-session-config-downloads-root.json",
        "invalid-session-config-user-profile-auth.json",
        "valid-action-navigate.json",
        "invalid-action-missing-session.json",
        "invalid-action-snapshot-null-capture-html.json",
        "valid-snapshot-minimal.json",
        "invalid-snapshot-bad-ref.json",
        "invalid-snapshot-null-ref-name.json",
        "valid-audit-event-allowed.json",
        "invalid-audit-event-missing-reason.json",
    }
    missing = required - names
    assert not missing, f"missing fixtures: {sorted(missing)}"
