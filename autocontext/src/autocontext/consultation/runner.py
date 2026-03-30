"""Consultation runner — calls secondary provider for advisory opinion (AC-212)."""
from __future__ import annotations

import logging
import re

from autocontext.consultation.types import ConsultationRequest, ConsultationResult
from autocontext.providers.base import CompletionResult, LLMProvider

logger = logging.getLogger(__name__)


class ConsultationRunner:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def consult(self, request: ConsultationRequest) -> ConsultationResult:
        """Call secondary provider for advisory opinion."""
        system_prompt = self._build_system_prompt(request)
        user_prompt = self._build_user_prompt(request)
        completion = self._provider.complete(system_prompt, user_prompt, temperature=0.3)
        return self._parse_response(completion)

    def _build_system_prompt(self, request: ConsultationRequest) -> str:
        return (
            "You are a strategy consultant for an iterative optimisation system. "
            "The system is experiencing a stall or uncertainty condition "
            f"(trigger: {request.trigger.value}). "
            "Provide your analysis using these markdown sections:\n"
            "## Critique\n## Alternative Hypothesis\n"
            "## Tiebreak Recommendation\n## Suggested Next Action"
        )

    def _build_user_prompt(self, request: ConsultationRequest) -> str:
        parts = [
            f"Run: {request.run_id}, Generation: {request.generation}",
            f"Trigger: {request.trigger.value}",
            f"Context: {request.context_summary}",
            f"Current strategy: {request.current_strategy_summary}",
        ]
        if request.score_history:
            parts.append(f"Score history: {request.score_history}")
        if request.gate_history:
            parts.append(f"Gate history: {request.gate_history}")
        return "\n".join(parts)

    def _parse_response(self, completion: CompletionResult) -> ConsultationResult:
        text = completion.text
        return ConsultationResult(
            critique=_extract_section(text, "Critique"),
            alternative_hypothesis=_extract_section(text, "Alternative Hypothesis"),
            tiebreak_recommendation=_extract_section(text, "Tiebreak Recommendation"),
            suggested_next_action=_extract_section(text, "Suggested Next Action"),
            raw_response=text,
            cost_usd=completion.cost_usd,
            model_used=completion.model or self._provider.default_model(),
        )


def _extract_section(text: str, heading: str) -> str:
    """Extract content under a markdown ## heading."""
    pattern = rf"##\s*{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""
