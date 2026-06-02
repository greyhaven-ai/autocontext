"""AC-697 mission lifecycle helpers (slice 2).

Mirrors ``ts/src/mission/lifecycle.ts`` + ``status-transitions.ts``.
Pure functions: status-transition table, derive next status from a
verifier result, and the "verifier threw" error-result builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autocontext.mission.types import MissionStatus, VerifierResult

__all__ = [
    "MissionStatusTransition",
    "build_verifier_error_result",
    "can_transition_mission_status",
    "derive_mission_status_from_verifier_result",
    "resolve_mission_status_transition",
]


# Transition table. Each row maps a previous status to the set of
# statuses the manager may transition into; mirrors the TS
# `ALLOWED_MISSION_STATUS_TRANSITIONS` table.
_ALLOWED_TRANSITIONS: dict[MissionStatus, frozenset[MissionStatus]] = {
    "active": frozenset(
        {
            "active",
            "paused",
            "completed",
            "failed",
            "canceled",
            "blocked",
            "budget_exhausted",
            "verifier_failed",
        }
    ),
    "paused": frozenset({"paused", "active", "canceled", "failed"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed", "active", "canceled"}),
    "canceled": frozenset({"canceled", "active"}),
    "blocked": frozenset({"blocked", "active", "canceled", "failed"}),
    "budget_exhausted": frozenset({"budget_exhausted", "active", "canceled"}),
    "verifier_failed": frozenset({"verifier_failed", "active", "failed", "canceled"}),
}


@dataclass(frozen=True)
class MissionStatusTransition:
    next_status: MissionStatus
    should_emit_status_change: bool


def can_transition_mission_status(previous_status: MissionStatus | None, next_status: MissionStatus) -> bool:
    if previous_status is None:
        return True
    return next_status in _ALLOWED_TRANSITIONS[previous_status]


def resolve_mission_status_transition(
    previous_status: MissionStatus | None, next_status: MissionStatus
) -> MissionStatusTransition:
    if not can_transition_mission_status(previous_status, next_status):
        raise ValueError(f"Invalid mission status transition: {previous_status} -> {next_status}")
    return MissionStatusTransition(
        next_status=next_status,
        should_emit_status_change=(previous_status is not None and previous_status != next_status),
    )


def derive_mission_status_from_verifier_result(
    result: VerifierResult,
) -> MissionStatus | None:
    """Mirrors TS `deriveMissionStatusFromVerifierResult`: a passing
    verifier closes the mission as `completed`; a failing verifier
    leaves the status untouched so the operator can decide whether
    to retry, pause, or cancel."""
    return "completed" if result.passed else None


def build_verifier_error_result(message: str, error_name: str) -> VerifierResult:
    """Mirrors TS `buildVerifierErrorResult`. A verifier that throws
    yields a failing result tagged with the original exception
    class name so log triage can route the failure category."""
    metadata: dict[str, Any] = {"verifierThrew": True, "errorName": error_name}
    return VerifierResult(
        passed=False,
        reason=f"Verifier error: {message}",
        suggestions=(),
        metadata=metadata,
    )
