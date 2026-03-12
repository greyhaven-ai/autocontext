"""Tests for ValidityGate — MTS-158: separate retry budget for invalid strategies."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autocontext.execution.harness_loader import HarnessLoader, HarnessValidationResult
from autocontext.harness.pipeline.validity_gate import ValidityGate, ValidityGateResult

# ── ValidityGateResult dataclass ─────────────────────────────────────────────


class TestValidityGateResult:
    def test_passed_result(self) -> None:
        r = ValidityGateResult(
            passed=True,
            errors=[],
            harness_errors=[],
            scenario_errors=[],
            retry_budget_remaining=5,
        )
        assert r.passed is True
        assert r.errors == []
        assert r.harness_errors == []
        assert r.scenario_errors == []
        assert r.retry_budget_remaining == 5

    def test_failed_result_with_errors(self) -> None:
        r = ValidityGateResult(
            passed=False,
            errors=["harness: bad move", "scenario: out of bounds"],
            harness_errors=["bad move"],
            scenario_errors=["out of bounds"],
            retry_budget_remaining=3,
        )
        assert r.passed is False
        assert len(r.errors) == 2
        assert r.harness_errors == ["bad move"]
        assert r.scenario_errors == ["out of bounds"]
        assert r.retry_budget_remaining == 3

    def test_frozen_dataclass(self) -> None:
        r = ValidityGateResult(
            passed=True, errors=[], harness_errors=[], scenario_errors=[], retry_budget_remaining=5,
        )
        with pytest.raises(AttributeError):
            r.passed = False  # type: ignore[misc]


# ── ValidityGate with no harness loader (scenario-only) ─────────────────────


class TestValidityGateScenarioOnly:
    def _make_scenario(self, *, valid: bool = True, reason: str = "") -> MagicMock:
        scenario = MagicMock()
        scenario.validate_actions.return_value = (valid, reason)
        scenario.initial_state.return_value = {"grid": []}
        return scenario

    def test_valid_strategy_passes(self) -> None:
        scenario = self._make_scenario(valid=True)
        gate = ValidityGate(harness_loader=None, scenario=scenario)
        result = gate.check({"moves": ["up"]})
        assert result.passed is True
        assert result.errors == []
        assert result.retry_budget_remaining == 5

    def test_invalid_strategy_fails(self) -> None:
        scenario = self._make_scenario(valid=False, reason="out of bounds")
        gate = ValidityGate(harness_loader=None, scenario=scenario)
        result = gate.check({"moves": ["invalid"]})
        assert result.passed is False
        assert "out of bounds" in result.errors[0]
        assert result.scenario_errors == ["out of bounds"]
        assert result.harness_errors == []

    def test_custom_max_retries(self) -> None:
        scenario = self._make_scenario(valid=True)
        gate = ValidityGate(harness_loader=None, scenario=scenario, max_retries=3)
        result = gate.check({"moves": ["up"]})
        assert result.retry_budget_remaining == 3

    def test_state_passed_to_scenario(self) -> None:
        scenario = self._make_scenario(valid=True)
        gate = ValidityGate(harness_loader=None, scenario=scenario)
        custom_state = {"grid": [[1, 2]], "turn": 3}
        gate.check({"moves": ["up"]}, state=custom_state)
        scenario.validate_actions.assert_called_once_with(custom_state, "challenger", {"moves": ["up"]})

    def test_default_state_from_scenario(self) -> None:
        scenario = self._make_scenario(valid=True)
        gate = ValidityGate(harness_loader=None, scenario=scenario)
        gate.check({"moves": ["up"]})
        scenario.initial_state.assert_called_once()
        scenario.validate_actions.assert_called_once()


# ── ValidityGate with harness loader ─────────────────────────────────────────


class TestValidityGateWithHarness:
    def _make_scenario(self, *, valid: bool = True, reason: str = "") -> MagicMock:
        scenario = MagicMock()
        scenario.validate_actions.return_value = (valid, reason)
        scenario.initial_state.return_value = {}
        return scenario

    def _make_harness(self, *, passed: bool = True, errors: list[str] | None = None) -> MagicMock:
        harness = MagicMock(spec=HarnessLoader)
        harness.validate_strategy.return_value = HarnessValidationResult(
            passed=passed, errors=errors or [],
        )
        return harness

    def test_both_pass(self) -> None:
        scenario = self._make_scenario(valid=True)
        harness = self._make_harness(passed=True)
        gate = ValidityGate(harness_loader=harness, scenario=scenario)
        result = gate.check({"moves": ["up"]})
        assert result.passed is True
        assert result.errors == []

    def test_harness_fails_scenario_passes(self) -> None:
        scenario = self._make_scenario(valid=True)
        harness = self._make_harness(passed=False, errors=["[check] bad format"])
        gate = ValidityGate(harness_loader=harness, scenario=scenario)
        result = gate.check({"moves": ["up"]})
        assert result.passed is False
        assert result.harness_errors == ["[check] bad format"]
        assert result.scenario_errors == []

    def test_scenario_fails_harness_passes(self) -> None:
        scenario = self._make_scenario(valid=False, reason="illegal move")
        harness = self._make_harness(passed=True)
        gate = ValidityGate(harness_loader=harness, scenario=scenario)
        result = gate.check({"moves": ["bad"]})
        assert result.passed is False
        assert result.scenario_errors == ["illegal move"]
        assert result.harness_errors == []

    def test_both_fail_combines_errors(self) -> None:
        scenario = self._make_scenario(valid=False, reason="illegal move")
        harness = self._make_harness(passed=False, errors=["[check] bad format"])
        gate = ValidityGate(harness_loader=harness, scenario=scenario)
        result = gate.check({"moves": ["bad"]})
        assert result.passed is False
        assert len(result.errors) == 2
        assert result.harness_errors == ["[check] bad format"]
        assert result.scenario_errors == ["illegal move"]


# ── Retry budget management ──────────────────────────────────────────────────


class TestValidityGateRetryBudget:
    def _make_gate(self, *, max_retries: int = 5) -> ValidityGate:
        scenario = MagicMock()
        scenario.validate_actions.return_value = (True, "")
        scenario.initial_state.return_value = {}
        return ValidityGate(harness_loader=None, scenario=scenario, max_retries=max_retries)

    def test_initial_budget(self) -> None:
        gate = self._make_gate(max_retries=5)
        result = gate.check({})
        assert result.retry_budget_remaining == 5

    def test_consume_retry_decrements(self) -> None:
        gate = self._make_gate(max_retries=3)
        assert gate.consume_retry() is True
        result = gate.check({})
        assert result.retry_budget_remaining == 2

    def test_consume_retry_exhausted(self) -> None:
        gate = self._make_gate(max_retries=2)
        assert gate.consume_retry() is True  # 2 -> 1
        assert gate.consume_retry() is True  # 1 -> 0
        assert gate.consume_retry() is False  # already 0

    def test_reset_restores_budget(self) -> None:
        gate = self._make_gate(max_retries=3)
        gate.consume_retry()
        gate.consume_retry()
        gate.reset()
        result = gate.check({})
        assert result.retry_budget_remaining == 3

    def test_budget_tracks_correctly_through_multiple_checks(self) -> None:
        gate = self._make_gate(max_retries=5)
        gate.consume_retry()
        gate.consume_retry()
        gate.consume_retry()
        result = gate.check({})
        assert result.retry_budget_remaining == 2

    def test_budget_independent_of_check_calls(self) -> None:
        """Calling check() does NOT consume the retry budget; only consume_retry() does."""
        gate = self._make_gate(max_retries=3)
        gate.check({})
        gate.check({})
        gate.check({})
        result = gate.check({})
        assert result.retry_budget_remaining == 3


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestValidityGateEdgeCases:
    def test_empty_strategy(self) -> None:
        scenario = MagicMock()
        scenario.validate_actions.return_value = (True, "")
        scenario.initial_state.return_value = {}
        gate = ValidityGate(harness_loader=None, scenario=scenario)
        result = gate.check({})
        assert result.passed is True

    def test_scenario_validate_actions_raises(self) -> None:
        """If scenario.validate_actions raises, it should be treated as a failure."""
        scenario = MagicMock()
        scenario.validate_actions.side_effect = RuntimeError("scenario crash")
        scenario.initial_state.return_value = {}
        gate = ValidityGate(harness_loader=None, scenario=scenario)
        result = gate.check({"moves": ["up"]})
        assert result.passed is False
        assert any("scenario crash" in e for e in result.errors)

    def test_harness_validate_strategy_raises(self) -> None:
        """If harness loader's validate_strategy raises, it should be treated as a failure."""
        scenario = MagicMock()
        scenario.validate_actions.return_value = (True, "")
        scenario.initial_state.return_value = {}
        harness = MagicMock(spec=HarnessLoader)
        harness.validate_strategy.side_effect = RuntimeError("harness crash")
        gate = ValidityGate(harness_loader=harness, scenario=scenario)
        result = gate.check({"moves": ["up"]})
        assert result.passed is False
        assert any("harness crash" in e for e in result.errors)

    def test_zero_max_retries(self) -> None:
        scenario = MagicMock()
        scenario.validate_actions.return_value = (True, "")
        scenario.initial_state.return_value = {}
        gate = ValidityGate(harness_loader=None, scenario=scenario, max_retries=0)
        result = gate.check({})
        assert result.retry_budget_remaining == 0
        assert gate.consume_retry() is False
