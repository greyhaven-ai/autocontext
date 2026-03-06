"""Playbook integrity guard — defense-in-depth for knowledge quality.

Inspired by Plankton's two-layer config protection that prevents agents from
modifying linting rules instead of fixing code.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardResult:
    """Result of a playbook integrity check."""

    approved: bool
    reason: str


class PlaybookGuard:
    """Validates playbook updates to prevent quality degradation."""

    REQUIRED_MARKERS = [
        ("<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->"),
        ("<!-- LESSONS_START -->", "<!-- LESSONS_END -->"),
        ("<!-- COMPETITOR_HINTS_START -->", "<!-- COMPETITOR_HINTS_END -->"),
    ]

    def __init__(self, max_shrink_ratio: float = 0.3) -> None:
        self._max_shrink = max_shrink_ratio

    def check(self, current: str, proposed: str) -> GuardResult:
        """Check if a proposed playbook update is safe."""
        # Check size shrinkage — empty proposed on non-empty current is 100% shrinkage
        if current:
            if not proposed:
                return GuardResult(
                    approved=False,
                    reason="Proposed playbook is empty (100% shrinkage)",
                )
            ratio = len(proposed) / len(current)
            if ratio < self._max_shrink:
                return GuardResult(
                    approved=False,
                    reason=f"Playbook shrink ratio {ratio:.2f} below threshold {self._max_shrink}",
                )

        # Check required markers preserved (both start and end)
        for start, end in self.REQUIRED_MARKERS:
            if start in current and start not in proposed:
                return GuardResult(
                    approved=False,
                    reason=f"Required marker '{start}' missing from proposed playbook",
                )
            if end in current and end not in proposed:
                return GuardResult(
                    approved=False,
                    reason=f"Required marker '{end}' missing from proposed playbook",
                )

        return GuardResult(approved=True, reason="")
