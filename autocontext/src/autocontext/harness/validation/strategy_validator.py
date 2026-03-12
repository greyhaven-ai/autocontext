"""Strategy validation with structured violation reports.

Inspired by Plankton's Phase 2 structured violation collection that normalizes
all linter outputs into a standard JSON format with fix hints.

NOTE: Not yet wired into the generation loop stages. Currently used only
in tests. TODO: integrate with loop/stages.py pre-tournament validation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StrategyViolation:
    """A single strategy violation with actionable fix hint."""

    violation_type: str  # "missing_field", "out_of_range", "schema_error", "scenario_invalid"
    field: str
    message: str
    fix_hint: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Structured report from strategy validation."""

    is_valid: bool
    violations: list[StrategyViolation] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Format violations as structured prompt context for retry."""
        if self.is_valid:
            return ""
        lines = ["--- STRATEGY VIOLATIONS ---"]
        for v in self.violations:
            lines.append(
                f"- [{v.violation_type}] {v.field}: {v.message}"
                f"\n  Fix: {v.fix_hint}"
            )
        lines.append("Fix ALL violations above before submitting.")
        return "\n".join(lines)


class StrategyValidator:
    """Validates strategies and produces structured violation reports."""

    def __init__(
        self,
        scenario_validate_fn: Callable[[dict], tuple[bool, str]] | None = None,
        required_fields: set[str] | None = None,
    ) -> None:
        self._scenario_fn = scenario_validate_fn
        self._required_fields = required_fields or set()

    @classmethod
    def from_required_fields(cls, fields: set[str]) -> StrategyValidator:
        """Create a validator that checks for required fields."""
        return cls(required_fields=fields)

    def validate(self, strategy: dict) -> ValidationReport:
        """Validate a strategy and return a structured report."""
        violations: list[StrategyViolation] = []

        for f in sorted(self._required_fields):
            if f not in strategy:
                violations.append(StrategyViolation(
                    violation_type="missing_field",
                    field=f,
                    message=f"Required field '{f}' is missing from strategy",
                    fix_hint=f"Add '{f}' to your strategy dict with an appropriate value",
                ))

        if self._scenario_fn is not None:
            valid, reason = self._scenario_fn(strategy)
            if not valid:
                violations.append(StrategyViolation(
                    violation_type="scenario_invalid",
                    field="strategy",
                    message=reason,
                    fix_hint="Adjust strategy to satisfy scenario constraints",
                ))

        return ValidationReport(
            is_valid=len(violations) == 0,
            violations=violations,
        )
