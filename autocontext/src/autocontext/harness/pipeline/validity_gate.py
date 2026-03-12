"""Validity gate with separate retry budget for invalid strategies.

The ValidityGate combines harness validation (from HarnessLoader) and scenario
validation (from ScenarioInterface.validate_actions) into a single binary
pass/fail check. Its retry budget is completely separate from the quality
gate's retry budget.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.execution.harness_loader import HarnessLoader
    from autocontext.scenarios.base import ScenarioInterface

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ValidityGateResult:
    """Result of a validity gate check."""

    passed: bool
    errors: list[str]
    harness_errors: list[str] = field(default_factory=list)
    scenario_errors: list[str] = field(default_factory=list)
    retry_budget_remaining: int = 0


class ValidityGate:
    """Binary validity gate with a separate retry budget.

    Combines harness validation (HarnessLoader.validate_strategy) and
    scenario validation (ScenarioInterface.validate_actions) into a
    single pass/fail. The retry budget is independent from the quality
    gate's retry budget.
    """

    def __init__(
        self,
        harness_loader: HarnessLoader | None,
        scenario: ScenarioInterface,
        *,
        max_retries: int = 5,
    ) -> None:
        self._harness_loader = harness_loader
        self._scenario = scenario
        self._max_retries = max_retries
        self._retries_remaining = max_retries

    def check(self, strategy: dict[str, Any], state: dict[str, Any] | None = None) -> ValidityGateResult:
        """Check strategy validity against harness and scenario validators.

        Args:
            strategy: The strategy dict to validate.
            state: Optional game state. If None, uses scenario.initial_state().

        Returns:
            ValidityGateResult with pass/fail, error details, and remaining budget.
        """
        harness_errors: list[str] = []
        scenario_errors: list[str] = []

        # --- Harness validation ---
        if self._harness_loader is not None:
            try:
                harness_result = self._harness_loader.validate_strategy(strategy, self._scenario)
                if not harness_result.passed:
                    harness_errors.extend(harness_result.errors)
            except Exception as exc:
                harness_errors.append(f"harness error: {exc}")

        # --- Scenario validation ---
        if state is None:
            state = self._scenario.initial_state()

        try:
            valid, reason = self._scenario.validate_actions(state, "challenger", strategy)
            if not valid and reason:
                scenario_errors.append(reason)
        except Exception as exc:
            scenario_errors.append(f"scenario error: {exc}")

        all_errors = harness_errors + scenario_errors
        passed = len(all_errors) == 0

        return ValidityGateResult(
            passed=passed,
            errors=all_errors,
            harness_errors=harness_errors,
            scenario_errors=scenario_errors,
            retry_budget_remaining=self._retries_remaining,
        )

    def consume_retry(self) -> bool:
        """Consume one retry from the validity budget.

        Returns True if a retry was available (and consumed), False if exhausted.
        """
        if self._retries_remaining <= 0:
            return False
        self._retries_remaining -= 1
        return True

    def reset(self) -> None:
        """Reset the retry budget to max_retries. Call at the start of each generation."""
        self._retries_remaining = self._max_retries
