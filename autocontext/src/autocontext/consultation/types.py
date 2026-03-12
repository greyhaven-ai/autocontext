"""Consultation types for AC-212: escalation-based provider consultation."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ConsultationTrigger(StrEnum):
    STAGNATION = "stagnation"
    JUDGE_UNCERTAINTY = "judge_uncertainty"
    PARSE_FAILURE = "parse_failure"
    OPERATOR_REQUEST = "operator_request"


@dataclass(slots=True)
class ConsultationRequest:
    run_id: str
    generation: int
    trigger: ConsultationTrigger
    context_summary: str
    current_strategy_summary: str
    score_history: list[float] = field(default_factory=list)
    gate_history: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConsultationResult:
    critique: str = ""
    alternative_hypothesis: str = ""
    tiebreak_recommendation: str = ""
    suggested_next_action: str = ""
    raw_response: str = ""
    cost_usd: float | None = None
    model_used: str = ""

    def to_advisory_markdown(self) -> str:
        """Render as markdown advisory artifact."""
        sections: list[str] = []
        if self.critique:
            sections.append(f"## Critique\n{self.critique}")
        if self.alternative_hypothesis:
            sections.append(f"## Alternative Hypothesis\n{self.alternative_hypothesis}")
        if self.tiebreak_recommendation:
            sections.append(f"## Tiebreak Recommendation\n{self.tiebreak_recommendation}")
        if self.suggested_next_action:
            sections.append(f"## Suggested Next Action\n{self.suggested_next_action}")
        if self.model_used:
            sections.append(f"---\n*Consultation model: {self.model_used}*")
        return "\n\n".join(sections) if sections else "*No advisory content.*"
