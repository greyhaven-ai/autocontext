"""Operator attestation for session sharing (AC-519).

No session is published without an explicit operator decision.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AttestationRecord:
    """Operator sign-off on a share bundle."""

    operator: str
    bundle_id: str
    decision: str  # "approved", "rejected", "auto_approved" (test/CI mode)
    timestamp: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "bundle_id": self.bundle_id,
            "decision": self.decision,
            "timestamp": self.timestamp,
            "reason": self.reason,
        }


def create_attestation(
    operator: str,
    bundle_id: str,
    decision: str,
    reason: str = "",
) -> AttestationRecord:
    """Create an attestation record with the current timestamp."""
    return AttestationRecord(
        operator=operator,
        bundle_id=bundle_id,
        decision=decision,
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        reason=reason,
    )
