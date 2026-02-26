from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(slots=True)
class JudgeResult:
    """Result from LLM judge evaluation."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    raw_responses: list[str] = field(default_factory=list)


_RESULT_START = "<!-- JUDGE_RESULT_START -->"
_RESULT_END = "<!-- JUDGE_RESULT_END -->"


class LLMJudge:
    """LLM-based judge for evaluating agent task outputs."""

    def __init__(
        self,
        model: str,
        rubric: str,
        llm_fn: Callable[[str, str], str],
        samples: int = 1,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.rubric = rubric
        self.llm_fn = llm_fn
        self.samples = max(1, samples)
        self.temperature = temperature

    def evaluate(
        self,
        task_prompt: str,
        agent_output: str,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
    ) -> JudgeResult:
        """Evaluate agent output by calling llm_fn N times and averaging."""
        system_prompt = (
            "You are an expert judge evaluating an AI agent's output. "
            "Evaluate the output against the provided rubric. "
        )
        if reference_context:
            system_prompt += (
                "You have been provided with authoritative reference context. "
                "You MUST evaluate factual accuracy against this reference. "
                "Any claims that contradict the reference context should be penalized heavily. "
                "Include a 'factual_accuracy' dimension in your scoring. "
            )
        system_prompt += (
            "Output your evaluation between <!-- JUDGE_RESULT_START --> and <!-- JUDGE_RESULT_END --> markers "
            'containing JSON: {"score": 0.0-1.0, "reasoning": "...", "dimensions": {"dim1": 0.0-1.0, ...}}'
        )
        user_prompt = self._build_judge_prompt(
            task_prompt, agent_output, reference_context, required_concepts
        )

        scores: list[float] = []
        reasonings: list[str] = []
        all_dims: list[dict[str, float]] = []
        raw_responses: list[str] = []

        for _ in range(self.samples):
            response = self.llm_fn(system_prompt, user_prompt)
            raw_responses.append(response)
            score, reasoning, dims = self._parse_judge_response(response)
            scores.append(score)
            reasonings.append(reasoning)
            all_dims.append(dims)

        avg_score = sum(scores) / len(scores)

        # Average dimension scores
        avg_dims: dict[str, float] = {}
        if all_dims:
            all_keys: set[str] = set()
            for d in all_dims:
                all_keys.update(d.keys())
            for key in all_keys:
                vals = [d[key] for d in all_dims if key in d]
                avg_dims[key] = sum(vals) / len(vals) if vals else 0.0

        # Ensure factual_accuracy dimension exists when reference context provided
        if reference_context and "factual_accuracy" not in avg_dims:
            avg_dims["factual_accuracy"] = avg_score

        combined_reasoning = "\n---\n".join(reasonings)

        return JudgeResult(
            score=avg_score,
            reasoning=combined_reasoning,
            dimension_scores=avg_dims,
            raw_responses=raw_responses,
        )

    def _build_judge_prompt(
        self,
        task_prompt: str,
        agent_output: str,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
    ) -> str:
        parts = [
            f"## Rubric\n{self.rubric}\n",
        ]
        if reference_context:
            parts.append(
                f"\n## Reference Context (Authoritative)\n{reference_context}\n"
            )
        if required_concepts:
            concepts_list = ", ".join(required_concepts)
            parts.append(
                f"\n## Required Concepts\nThe output MUST correctly address these concepts: {concepts_list}\n"
            )
        parts.append(f"\n## Task Prompt\n{task_prompt}\n")
        parts.append(f"\n## Agent Output\n{agent_output}\n")
        parts.append(
            "\nEvaluate the agent's output against the rubric. "
            "Provide your evaluation between <!-- JUDGE_RESULT_START --> and <!-- JUDGE_RESULT_END --> markers."
        )
        return "\n".join(parts)

    def _parse_judge_response(self, response: str) -> tuple[float, str, dict[str, float]]:
        """Parse judge response, extracting JSON between markers."""
        pattern = re.compile(
            re.escape(_RESULT_START) + r"\s*(.*?)\s*" + re.escape(_RESULT_END),
            re.DOTALL,
        )
        match = pattern.search(response)
        if not match:
            return 0.0, "Failed to parse judge response: missing JUDGE_RESULT markers", {}

        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            return 0.0, "Failed to parse judge response: invalid JSON", {}

        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        reasoning = str(data.get("reasoning", ""))
        dimensions = data.get("dimensions", {})
        dim_scores = {str(k): max(0.0, min(1.0, float(v))) for k, v in dimensions.items()}

        return score, reasoning, dim_scores
