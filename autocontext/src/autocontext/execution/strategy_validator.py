"""Strategy pre-validation via self-play dry-run before tournament."""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings
    from autocontext.scenarios.base import ScenarioInterface


@dataclass(slots=True)
class ValidationResult:
    """Outcome of a strategy pre-validation dry-run."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    match_summary: str = ""


class StrategyValidator:
    """Pre-validates strategies via self-play dry-run before tournament."""

    def __init__(self, scenario: ScenarioInterface, settings: AppSettings) -> None:
        self.scenario = scenario
        self.settings = settings

    def validate(self, strategy: dict[str, Any]) -> ValidationResult:
        """Run self-play dry-run: execute_match(strategy, seed=0).

        For code strategies (__code__ key), skip the dry-run and pass through
        since code strategies are validated at execution time.

        Returns ValidationResult with errors if match raises or produces
        validation_errors.
        """
        # Code strategies skip dry-run
        if "__code__" in strategy:
            return ValidationResult(passed=True)

        try:
            result = self.scenario.execute_match(strategy, seed=0)
        except Exception:
            logger.debug("execution.strategy_validator: caught Exception", exc_info=True)
            tb = traceback.format_exc()
            return ValidationResult(passed=False, errors=[tb])

        if result.validation_errors:
            return ValidationResult(passed=False, errors=list(result.validation_errors))

        return ValidationResult(passed=True, match_summary=result.summary)

    def format_revision_prompt(self, result: ValidationResult, original_strategy: dict[str, Any]) -> str:
        """Format error trace into a revision prompt for the competitor."""
        lines = [
            "Your strategy failed pre-validation. Please fix the issues below and resubmit.",
            "",
            "--- ERRORS ---",
        ]
        for err in result.errors:
            lines.append(err)
        lines.append("")
        lines.append("--- ORIGINAL STRATEGY ---")
        lines.append(json.dumps(original_strategy, indent=2, sort_keys=True))
        lines.append("")
        lines.append("Please produce a corrected strategy that avoids these errors.")
        return "\n".join(lines)
