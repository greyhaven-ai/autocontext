"""AC-697 mission lifecycle helper tests (slice 2).

Mirrors the unit-test surface for ``ts/src/mission/lifecycle.ts`` +
``status-transitions.ts`` + ``verification-workflow.ts``: pure
function tests covering the transition table, verifier-result -> next
status derivation, and the workflow outcome builders.
"""

from __future__ import annotations

import pytest

from autocontext.mission import (
    VerifierResult,
    build_missing_verifier_outcome,
    build_verifier_error_result,
    can_transition_mission_status,
    derive_mission_status_from_verifier_result,
    resolve_mission_status_transition,
    resolve_mission_verification_error_outcome,
    resolve_mission_verification_outcome,
)

# ---------------------------------------------------------------------------
# transition table
# ---------------------------------------------------------------------------


def test_no_previous_status_always_transitions() -> None:
    """The TS table treats ``previousStatus === undefined`` as a
    wildcard so new missions can land in any state. The Python port
    mirrors that."""
    for next_status in (
        "active",
        "paused",
        "completed",
        "failed",
        "canceled",
        "blocked",
        "budget_exhausted",
        "verifier_failed",
    ):
        assert can_transition_mission_status(None, next_status) is True


def test_active_to_paused_is_allowed() -> None:
    transition = resolve_mission_status_transition("active", "paused")
    assert transition.next_status == "paused"
    assert transition.should_emit_status_change is True


def test_paused_to_completed_is_rejected() -> None:
    with pytest.raises(ValueError, match="paused -> completed"):
        resolve_mission_status_transition("paused", "completed")


def test_completed_is_terminal() -> None:
    """The TS table only allows completed -> completed (self-loop, no
    re-open)."""
    for forbidden in ("active", "paused", "canceled", "failed"):
        assert can_transition_mission_status("completed", forbidden) is False
    transition = resolve_mission_status_transition("completed", "completed")
    assert transition.should_emit_status_change is False


def test_self_loop_does_not_emit_status_change() -> None:
    transition = resolve_mission_status_transition("active", "active")
    assert transition.next_status == "active"
    assert transition.should_emit_status_change is False


# ---------------------------------------------------------------------------
# verifier result -> status derivation
# ---------------------------------------------------------------------------


def test_passing_verifier_result_completes_the_mission() -> None:
    result = VerifierResult(passed=True, reason="green")
    assert derive_mission_status_from_verifier_result(result) == "completed"


def test_failing_verifier_result_leaves_status_untouched() -> None:
    result = VerifierResult(passed=False, reason="tests failed")
    assert derive_mission_status_from_verifier_result(result) is None


# ---------------------------------------------------------------------------
# error result builder
# ---------------------------------------------------------------------------


def test_verifier_error_result_carries_error_name_and_message() -> None:
    result = build_verifier_error_result("boom", "RuntimeError")
    assert result.passed is False
    assert "Verifier error: boom" in result.reason
    assert result.metadata["verifierThrew"] is True
    assert result.metadata["errorName"] == "RuntimeError"


# ---------------------------------------------------------------------------
# verification workflow outcomes
# ---------------------------------------------------------------------------


def test_missing_verifier_outcome_is_failing_with_no_next_status() -> None:
    outcome = build_missing_verifier_outcome()
    assert outcome.result.passed is False
    assert outcome.result.reason == "No verifier registered"
    assert outcome.next_status is None


def test_pass_outcome_derives_completed_next_status() -> None:
    outcome = resolve_mission_verification_outcome(VerifierResult(passed=True, reason="green"))
    assert outcome.next_status == "completed"


def test_fail_outcome_keeps_next_status_none() -> None:
    outcome = resolve_mission_verification_outcome(VerifierResult(passed=False, reason="red"))
    assert outcome.next_status is None


def test_error_outcome_wraps_thrown_error_and_keeps_next_status_none() -> None:
    outcome = resolve_mission_verification_error_outcome("boom", "OSError")
    assert outcome.result.passed is False
    assert outcome.result.metadata["errorName"] == "OSError"
    assert outcome.next_status is None
