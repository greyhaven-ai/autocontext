"""Trigger detection for provider consultation (AC-212)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from autocontext.consultation.types import ConsultationTrigger

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings

_STALL_DECISIONS = frozenset({"rollback", "retry"})


def detect_consultation_triggers(
    gate_history: list[str],
    score_history: list[float],
    settings: AppSettings,
) -> list[ConsultationTrigger]:
    """Check if any consultation triggers are active.

    Returns a list of active triggers (may be empty).
    """
    triggers: list[ConsultationTrigger] = []

    threshold = settings.consultation_stagnation_threshold

    # Stagnation: N consecutive rollback/retry at the tail of gate_history
    if len(gate_history) >= threshold:
        tail = gate_history[-threshold:]
        if all(d in _STALL_DECISIONS for d in tail):
            triggers.append(ConsultationTrigger.STAGNATION)

    # Judge uncertainty: score variance in last 3 gens is very low but no advance
    if len(score_history) >= 3 and len(gate_history) >= 3:
        recent_scores = score_history[-3:]
        recent_gates = gate_history[-3:]
        has_advance = any(g == "advance" for g in recent_gates)
        if not has_advance:
            mean = sum(recent_scores) / len(recent_scores)
            variance = sum((s - mean) ** 2 for s in recent_scores) / len(recent_scores)
            if variance < 0.01:
                triggers.append(ConsultationTrigger.JUDGE_UNCERTAINTY)

    return triggers
