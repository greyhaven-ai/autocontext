"""AC-697 mission verification workflow (slice 2).

Mirrors ``ts/src/mission/verification-workflow.ts``. Pure functions
that classify a verifier outcome into a (``result``, ``next_status``)
pair the manager can then persist + apply.
"""

from __future__ import annotations

from dataclasses import dataclass

from autocontext.mission.lifecycle import (
    build_verifier_error_result,
    derive_mission_status_from_verifier_result,
)
from autocontext.mission.types import MissionStatus, VerifierResult

__all__ = [
    "MissionVerificationOutcome",
    "build_missing_verifier_outcome",
    "resolve_mission_verification_error_outcome",
    "resolve_mission_verification_outcome",
]


@dataclass(frozen=True)
class MissionVerificationOutcome:
    result: VerifierResult
    next_status: MissionStatus | None


def build_missing_verifier_outcome() -> MissionVerificationOutcome:
    return MissionVerificationOutcome(
        result=VerifierResult(
            passed=False,
            reason="No verifier registered",
            suggestions=(),
            metadata={},
        ),
        next_status=None,
    )


def resolve_mission_verification_outcome(
    result: VerifierResult,
) -> MissionVerificationOutcome:
    return MissionVerificationOutcome(
        result=result,
        next_status=derive_mission_status_from_verifier_result(result),
    )


def resolve_mission_verification_error_outcome(message: str, error_name: str) -> MissionVerificationOutcome:
    return MissionVerificationOutcome(
        result=build_verifier_error_result(message, error_name),
        next_status=None,
    )
