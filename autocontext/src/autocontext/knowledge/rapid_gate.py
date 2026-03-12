from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RapidGateResult:
    decision: str  # "advance" or "rollback"
    delta: float
    reason: str


def rapid_gate(current_best: float, previous_best: float) -> RapidGateResult:
    """Binary keep/discard gate for rapid exploration mode.

    Any positive improvement -> advance, otherwise rollback.
    No retry logic.
    """
    delta = current_best - previous_best
    if delta > 0:
        return RapidGateResult(
            decision="advance",
            delta=delta,
            reason=f"Score improved by {delta:+.4f}",
        )
    return RapidGateResult(
        decision="rollback",
        delta=delta,
        reason=f"No improvement (delta={delta:+.4f})",
    )


def should_transition_to_linear(generation_index: int, rapid_gens: int) -> bool:
    """Check if rapid mode should auto-transition to linear.

    Args:
        generation_index: Current generation (1-based)
        rapid_gens: Number of rapid gens before transition (0 = never transition)
    """
    if rapid_gens <= 0:
        return False
    return generation_index >= rapid_gens
