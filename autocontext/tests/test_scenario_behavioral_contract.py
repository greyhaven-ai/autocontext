"""Track A — AC-527: Scenario behavioral contract.

A canonical operator-loop escalation scenario must fail clearly when
required behaviors (escalation, clarification) are missing, even if
the underlying execution technically succeeded.

Tests the pure-domain ScenarioBehavioralContract evaluator, which takes
a description and a summary dict and returns a ContractResult.
"""

from __future__ import annotations

import pytest

from autocontext.scenarios.family_contracts import (
    ContractResult,
    ScenarioBehavioralContract,
    get_family_contract,
)


class TestOperatorLoopContract:
    """Operator-loop behavioral contract: escalation required when prompt triggers it."""

    @pytest.fixture()
    def contract(self) -> ScenarioBehavioralContract:
        c = get_family_contract("operator_loop")
        assert c is not None
        return c

    # ------------------------------------------------------------------
    # Escalation triggers
    # ------------------------------------------------------------------

    def test_flags_missing_escalation_when_prompt_requires_it(self, contract: ScenarioBehavioralContract) -> None:
        """AC-527 canonical case: 'escalate to a human operator' with 0 escalations."""
        description = (
            "simulate a customer support escalation where the AI agent must "
            "escalate to a human operator, wait for operator input, then "
            "continue with the operator's guidance"
        )
        summary = {"score": 0.3, "escalation_count": 0, "clarification_count": 0}

        result = contract.evaluate(description, summary)
        assert not result.satisfied
        assert "escalation" in result.missing_signals
        assert result.score_ceiling is not None
        assert result.score_ceiling <= 0.3

    def test_passes_when_escalation_prompt_met(self, contract: ScenarioBehavioralContract) -> None:
        """Positive control: escalation occurred."""
        description = "simulate a customer support escalation where the AI agent must escalate to a human operator"
        summary = {"score": 0.8, "escalation_count": 2, "clarification_count": 0}

        result = contract.evaluate(description, summary)
        assert result.satisfied
        assert result.missing_signals == []

    def test_no_escalation_required_when_prompt_does_not_ask(self, contract: ScenarioBehavioralContract) -> None:
        """A prompt about autonomous handling should NOT require escalation."""
        description = "handle all requests autonomously without operator involvement"
        summary = {"score": 0.7, "escalation_count": 0, "clarification_count": 0}

        result = contract.evaluate(description, summary)
        assert result.satisfied

    # ------------------------------------------------------------------
    # Clarification triggers
    # ------------------------------------------------------------------

    def test_missing_clarification_is_warning_not_failure(self, contract: ScenarioBehavioralContract) -> None:
        """Clarification requirements are 'recommended', not 'required'.
        Missing them attaches a warning but doesn't cap the score."""
        description = "handle requests with incomplete inputs, asking clarifying questions when needed"
        summary = {"score": 0.8, "escalation_count": 0, "clarification_count": 0}

        result = contract.evaluate(description, summary)
        # The escalation trigger is NOT present, so only clarification is relevant.
        # Clarification is recommended, not required — satisfied is True.
        assert result.satisfied
        # But warnings should mention the missing clarification.
        assert any("clarification" in w.lower() for w in result.warnings)

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_handles_missing_count_keys_gracefully(self, contract: ScenarioBehavioralContract) -> None:
        """Summary without escalation_count/clarification_count should be
        treated as 0 (worst case)."""
        description = "escalate to a human operator when the customer requests a refund"
        summary = {"score": 0.5}  # no count keys

        result = contract.evaluate(description, summary)
        assert not result.satisfied
        assert "escalation" in result.missing_signals

    def test_no_contract_for_unregistered_family(self) -> None:
        """get_family_contract returns None for families without a contract."""
        assert get_family_contract("game") is None
        assert get_family_contract("bogus") is None


class TestContractResult:
    """ContractResult value object."""

    def test_satisfied_result_has_no_missing_signals(self) -> None:
        result = ContractResult(satisfied=True, missing_signals=[], warnings=[], score_ceiling=None, reason="OK")
        assert result.satisfied
        assert result.score_ceiling is None

    def test_violated_result_has_missing_signals_and_ceiling(self) -> None:
        result = ContractResult(
            satisfied=False,
            missing_signals=["escalation"],
            warnings=[],
            score_ceiling=0.3,
            reason="Escalation required but not observed",
        )
        assert not result.satisfied
        assert result.score_ceiling == 0.3
