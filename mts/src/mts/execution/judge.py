from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from mts.providers.base import LLMProvider
from mts.providers.callable_wrapper import CallableProvider

logger = logging.getLogger(__name__)


ParseMethod = Literal["raw_json", "code_block", "markers", "plaintext", "none"]


@dataclass(slots=True)
class JudgeResult:
    """Result from LLM judge evaluation."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    raw_responses: list[str] = field(default_factory=list)
    parse_method: ParseMethod = "none"
    internal_retries: int = 0


_RESULT_START = "<!-- JUDGE_RESULT_START -->"
_RESULT_END = "<!-- JUDGE_RESULT_END -->"


class LLMJudge:
    """LLM-based judge for evaluating agent task outputs.

    Accepts either a ``provider: LLMProvider`` or a legacy
    ``llm_fn: Callable[[str, str], str]`` for backward compatibility.
    """

    def __init__(
        self,
        model: str,
        rubric: str,
        llm_fn: Callable[[str, str], str] | None = None,
        provider: LLMProvider | None = None,
        samples: int = 1,
        temperature: float = 0.0,
    ) -> None:
        if provider is not None:
            self.provider = provider
        elif llm_fn is not None:
            self.provider = CallableProvider(llm_fn, model_name=model)
        else:
            raise ValueError("Either 'provider' or 'llm_fn' must be provided")

        self.model = model
        self.rubric = rubric
        self.samples = max(1, samples)
        self.temperature = temperature

        # Backward-compatible property
        self.llm_fn = llm_fn

    def evaluate(
        self,
        task_prompt: str,
        agent_output: str,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
    ) -> JudgeResult:
        """Evaluate agent output by calling the provider N times and averaging."""
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
            task_prompt, agent_output, reference_context, required_concepts, calibration_examples
        )

        scores: list[float] = []
        reasonings: list[str] = []
        all_dims: list[dict[str, float]] = []
        raw_responses: list[str] = []
        total_internal_retries = 0
        last_parse_method: ParseMethod = "none"

        for _ in range(self.samples):
            dims: dict[str, float] = {}
            score, reasoning = 0.0, ""
            sample_parse_method: ParseMethod = "none"
            # Retry up to 2 times on parse failure
            for attempt in range(2):
                result = self.provider.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=self.model,
                    temperature=self.temperature,
                )
                response = result.text
                raw_responses.append(response)
                score, reasoning, dims, sample_parse_method = self._parse_judge_response(response)
                if score > 0.0 or "Failed to parse" not in reasoning:
                    break
                total_internal_retries += 1
                logger.warning("judge parse failed (attempt %d), retrying", attempt + 1)
            scores.append(score)
            reasonings.append(reasoning)
            all_dims.append(dims)
            last_parse_method = sample_parse_method

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
            parse_method=last_parse_method,
            internal_retries=total_internal_retries,
        )

    def _build_judge_prompt(
        self,
        task_prompt: str,
        agent_output: str,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
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
        if calibration_examples:
            cal_lines = ["\n## Calibration Examples (Human-Scored)\n"]
            cal_lines.append(
                "The following are real outputs scored by a human reviewer. "
                "Use these to calibrate your scoring — match the human's standards.\n"
            )
            for i, ex in enumerate(calibration_examples, 1):
                score = ex.get("human_score", "N/A")
                notes = ex.get("human_notes", "")
                output_snippet = ex.get("agent_output", "")[:200]
                cal_lines.append(
                    f"**Example {i}** — Score: {score}\n"
                    f"Human notes: {notes}\n"
                    f"Output snippet: {output_snippet}...\n"
                )
            parts.append("\n".join(cal_lines))
        parts.append(f"\n## Task Prompt\n{task_prompt}\n")
        parts.append(f"\n## Agent Output\n{agent_output}\n")
        parts.append(
            "\nEvaluate the agent's output against the rubric. "
            "Provide your evaluation between <!-- JUDGE_RESULT_START --> and <!-- JUDGE_RESULT_END --> markers.\n\n"
            "You MUST use exactly this format:\n"
            "<!-- JUDGE_RESULT_START -->\n"
            '{"score": 0.85, "reasoning": "Your detailed reasoning here", '
            '"dimensions": {"dimension_name": 0.9, "other_dimension": 0.8}}\n'
            "<!-- JUDGE_RESULT_END -->\n\n"
            "The score and all dimension values must be between 0.0 and 1.0. "
            "Include dimension scores that match the rubric criteria."
        )
        return "\n".join(parts)

    def _parse_judge_response(self, response: str) -> tuple[float, str, dict[str, float], ParseMethod]:
        """Parse judge response using multiple strategies.

        Strategies (tried in order):
        1. Raw JSON: find a JSON object with "score" key anywhere in response
        2. Code block: extract JSON from ```json ... ``` blocks
        3. Marker-based: extract JSON between <!-- JUDGE_RESULT_START/END -->
        4. Plain text: regex for "score": X.XX or "Score: X.XX" patterns
        """
        # Strategy 1: Raw JSON object with "score" key (most common in practice)
        data = self._try_raw_json_parse(response)
        if data is not None:
            return self._extract_from_dict(data, "raw_json")

        # Strategy 2: JSON code block
        data = self._try_code_block_parse(response)
        if data is not None:
            return self._extract_from_dict(data, "code_block")

        # Strategy 3: Marker-based
        data = self._try_marker_parse(response)
        if data is not None:
            return self._extract_from_dict(data, "markers")

        # Strategy 4: Plain text score extraction
        result = self._try_plaintext_parse(response)
        if result is not None:
            return (*result, "plaintext")

        return 0.0, "Failed to parse judge response: no parseable score found", {}, "none"

    @staticmethod
    def _try_marker_parse(response: str) -> dict | None:
        """Strategy 1: Extract JSON between JUDGE_RESULT markers."""
        pattern = re.compile(
            re.escape(_RESULT_START) + r"\s*(.*?)\s*" + re.escape(_RESULT_END),
            re.DOTALL,
        )
        match = pattern.search(response)
        if not match:
            return None
        try:
            data: dict = json.loads(match.group(1))
            return data
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _try_code_block_parse(response: str) -> dict | None:
        """Strategy 2: Extract JSON from ```json ... ``` code blocks."""
        pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
        for match in pattern.finditer(response):
            try:
                data = json.loads(match.group(1).strip())
                if isinstance(data, dict) and "score" in data:
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    @staticmethod
    def _try_raw_json_parse(response: str) -> dict | None:
        """Strategy 3: Find a JSON object containing 'score' key."""
        # Look for JSON objects in the response
        for match in re.finditer(r'\{[^{}]*"score"[^{}]*\}', response):
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict) and "score" in data:
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        # Try nested objects (with dimensions)
        for match in re.finditer(r'\{(?:[^{}]|\{[^{}]*\})*"score"(?:[^{}]|\{[^{}]*\})*\}', response):
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict) and "score" in data:
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    @staticmethod
    def _try_plaintext_parse(response: str) -> tuple[float, str, dict[str, float]] | None:
        """Strategy 4: Extract score from plain text patterns."""
        # Match patterns like "Score: 0.85" or "Overall score: 0.9"
        patterns = [
            r'(?:overall\s+)?score[:\s]+([01](?:\.\d+)?)',
            r'"score"\s*:\s*([01](?:\.\d+)?)',
            r'(\d\.\d+)\s*/\s*1\.0',
        ]
        for pat in patterns:
            match = re.search(pat, response, re.IGNORECASE)
            if match:
                try:
                    score = float(match.group(1))
                    if 0.0 <= score <= 1.0:
                        # Use the full response as reasoning since we couldn't parse structured
                        reasoning = response[:500] if len(response) > 500 else response
                        return score, reasoning, {}
                except (ValueError, IndexError):
                    continue
        return None

    @staticmethod
    def _extract_from_dict(
        data: dict, method: ParseMethod,
    ) -> tuple[float, str, dict[str, float], ParseMethod]:
        """Extract score, reasoning, dimensions, and parse method from a parsed dict."""
        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        reasoning = str(data.get("reasoning", ""))
        dimensions = data.get("dimensions", {})
        dim_scores: dict[str, float] = {}
        for k, v in dimensions.items():
            try:
                dim_scores[str(k)] = max(0.0, min(1.0, float(v)))
            except (ValueError, TypeError):
                continue
        return score, reasoning, dim_scores, method
