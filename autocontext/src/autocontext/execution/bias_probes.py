from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, computed_field

from autocontext.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class BiasProbeResult(BaseModel):
    """Result of a single bias probe."""

    probe_type: str  # "position" | "style" | "length"
    detected: bool
    magnitude: float  # 0.0-1.0, how strong the bias is
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class BiasReport(BaseModel):
    """Aggregated bias probe results."""

    probes_run: int = 0
    probes_failed: int = 0
    results: list[BiasProbeResult] = Field(default_factory=list)
    any_bias_detected: bool = False

    @computed_field(return_type=list[str])
    def bias_types_detected(self) -> list[str]:
        """Return probe types where bias was detected."""
        return [r.probe_type for r in self.results if r.detected]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def run_position_bias_probe(
    provider: LLMProvider,
    model: str,
    system_prompt: str,
    candidate_a: str,
    candidate_b: str,
    rubric: str,
    temperature: float = 0.0,
) -> BiasProbeResult:
    """Detect position bias by comparing A-then-B vs B-then-A ordering.

    Presents the same two candidates in both orderings.
    If the judge consistently prefers whichever is presented first (or second),
    that indicates position bias.
    """
    from autocontext.execution.judge import LLMJudge

    # Order 1: A first
    prompt_ab = (
        f"## Rubric\n{rubric}\n\n"
        f"## Candidate 1\n{candidate_a}\n\n"
        f"## Candidate 2\n{candidate_b}\n\n"
        "Score Candidate 1 between 0.0 and 1.0.\n"
        'Output: <!-- JUDGE_RESULT_START -->{"score": X.X, "reasoning": "..."}<!-- JUDGE_RESULT_END -->'
    )

    # Order 2: B first
    prompt_ba = (
        f"## Rubric\n{rubric}\n\n"
        f"## Candidate 1\n{candidate_b}\n\n"
        f"## Candidate 2\n{candidate_a}\n\n"
        "Score Candidate 1 between 0.0 and 1.0.\n"
        'Output: <!-- JUDGE_RESULT_START -->{"score": X.X, "reasoning": "..."}<!-- JUDGE_RESULT_END -->'
    )

    result_ab = provider.complete(
        system_prompt=system_prompt, user_prompt=prompt_ab, model=model, temperature=temperature,
    )
    result_ba = provider.complete(
        system_prompt=system_prompt, user_prompt=prompt_ba, model=model, temperature=temperature,
    )

    # Parse scores
    judge = LLMJudge(model=model, rubric=rubric, provider=provider)
    score_ab = judge._parse_judge_response(result_ab.text)[0]
    score_ba = judge._parse_judge_response(result_ba.text)[0]

    # In a fair judge: score_ab ~ 1 - score_ba (since candidates swap positions)
    # Position bias: both score_ab and score_ba are high (judge always prefers first/second)
    # Magnitude = |score_ba - (1 - score_ab)| / 2.0, normalized to 0-1
    expected_ba = 1.0 - score_ab
    magnitude = abs(score_ba - expected_ba) / 2.0
    detected = magnitude > 0.1  # > 10% position effect

    return BiasProbeResult(
        probe_type="position",
        detected=detected,
        magnitude=min(1.0, magnitude),
        details=f"A-first score: {score_ab:.3f}, B-first score for B: {score_ba:.3f}, magnitude: {magnitude:.3f}",
    )
