"""Per-family behavioral contracts (AC-527).

A ScenarioBehavioralContract declares what signals a completed run must
exhibit for a given family. If the contract is violated, the engine marks
the run ``incomplete`` instead of ``completed`` and caps the score.

This is the single enforcement point — every simulation/run that passes
through the engine is evaluated against its family's contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ContractResult:
    """Outcome of evaluating a behavioral contract against a run summary."""

    satisfied: bool
    missing_signals: list[str]
    warnings: list[str]
    score_ceiling: float | None  # None = no cap
    reason: str


@dataclass(slots=True)
class SignalRequirement:
    """A behavioral signal that a family may require of a completed run."""

    name: str
    trigger_pattern: re.Pattern[str]
    summary_key: str
    min_count: int = 1
    severity: str = "required"  # "required" | "recommended"


class ScenarioBehavioralContract:
    """Evaluates whether a run exhibits required behaviors for its family."""

    def __init__(self, family: str, requirements: list[SignalRequirement], default_score_ceiling: float = 0.3) -> None:
        self.family = family
        self.requirements = requirements
        self.default_score_ceiling = default_score_ceiling

    def evaluate(self, description: str, summary: dict[str, Any]) -> ContractResult:
        """Evaluate the contract against a description and summary dict.

        Returns ContractResult with satisfaction status, missing signals,
        warnings, and optional score ceiling.
        """
        description_lower = description.lower()
        missing_signals: list[str] = []
        warnings: list[str] = []

        for req in self.requirements:
            if not req.trigger_pattern.search(description_lower):
                continue

            observed_count = summary.get(req.summary_key, 0)
            if observed_count >= req.min_count:
                continue

            if req.severity == "required":
                missing_signals.append(req.name)
            else:
                warnings.append(
                    f"Recommended signal '{req.name}' not observed "
                    f"(expected >= {req.min_count} {req.summary_key}, got {observed_count})"
                )

        if missing_signals:
            return ContractResult(
                satisfied=False,
                missing_signals=missing_signals,
                warnings=warnings,
                score_ceiling=self.default_score_ceiling,
                reason=f"Missing required signals: {', '.join(missing_signals)}",
            )

        return ContractResult(
            satisfied=True,
            missing_signals=[],
            warnings=warnings,
            score_ceiling=None,
            reason="OK",
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ESCALATION_TRIGGERS = re.compile(
    r"must.escalat|should.escalat|needs?.to.escalat|"
    r"escalate.to.(?:a.)?(?:human|operator)|"
    r"hand.off.to.(?:human|operator)|"
    r"defer.to.(?:human|operator)|"
    r"wait.for.operator.input"
)

_CLARIFICATION_TRIGGERS = re.compile(
    r"clarif|ambiguous|incomplete.input|ask.*(question|user)|"
    r"gather.more.info|missing.information"
)

FAMILY_CONTRACTS: dict[str, ScenarioBehavioralContract] = {
    "operator_loop": ScenarioBehavioralContract(
        family="operator_loop",
        requirements=[
            SignalRequirement(
                name="escalation",
                trigger_pattern=_ESCALATION_TRIGGERS,
                summary_key="escalation_count",
                min_count=1,
                severity="required",
            ),
            SignalRequirement(
                name="clarification",
                trigger_pattern=_CLARIFICATION_TRIGGERS,
                summary_key="clarification_count",
                min_count=1,
                severity="recommended",
            ),
        ],
    ),
}


def get_family_contract(family: str) -> ScenarioBehavioralContract | None:
    """Look up the behavioral contract for a family. Returns None if no contract defined."""
    return FAMILY_CONTRACTS.get(family)
