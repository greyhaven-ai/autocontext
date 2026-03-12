"""Tests for strategy validation with structured violation reports."""
from __future__ import annotations

from autocontext.harness.validation.strategy_validator import StrategyValidator


def test_valid_strategy_passes() -> None:
    def validate_fn(strategy: dict) -> tuple[bool, str]:
        return True, ""

    validator = StrategyValidator(validate_fn)
    report = validator.validate({"aggression": 0.5, "defense": 0.5})
    assert report.is_valid
    assert len(report.violations) == 0


def test_invalid_strategy_returns_violations() -> None:
    def validate_fn(strategy: dict) -> tuple[bool, str]:
        return False, "aggression must be between 0 and 1"

    validator = StrategyValidator(validate_fn)
    report = validator.validate({"aggression": 1.5})
    assert not report.is_valid
    assert len(report.violations) >= 1
    assert "aggression" in report.violations[0].message


def test_missing_field_detected() -> None:
    required = {"aggression", "defense"}
    validator = StrategyValidator.from_required_fields(required)
    report = validator.validate({"aggression": 0.5})
    assert not report.is_valid
    assert any(v.violation_type == "missing_field" for v in report.violations)


def test_violation_has_fix_hint() -> None:
    required = {"aggression", "defense"}
    validator = StrategyValidator.from_required_fields(required)
    report = validator.validate({})
    assert all(v.fix_hint for v in report.violations)


def test_report_to_prompt_context() -> None:
    """Violations format into structured prompt context for retry."""
    required = {"aggression", "defense"}
    validator = StrategyValidator.from_required_fields(required)
    report = validator.validate({})
    prompt = report.to_prompt_context()
    assert "STRATEGY VIOLATIONS" in prompt
    assert "missing_field" in prompt
