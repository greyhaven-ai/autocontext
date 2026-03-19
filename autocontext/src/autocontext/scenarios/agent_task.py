from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentTaskResult:
    """Result of evaluating an agent's output on a task."""

    score: float  # 0.0 to 1.0
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    internal_retries: int = 0
    evaluator_guardrail: dict[str, Any] | None = None


class AgentTaskInterface(ABC):
    """Abstract interface for agent task scenarios."""

    @abstractmethod
    def get_task_prompt(self, state: dict) -> str:
        """Return the task prompt for the agent."""

    @abstractmethod
    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        """Evaluate the agent's output against the task criteria."""

    @abstractmethod
    def get_rubric(self) -> str:
        """Return the evaluation rubric."""

    @abstractmethod
    def initial_state(self, seed: int | None = None) -> dict:
        """Return the initial state for this task."""

    @abstractmethod
    def describe_task(self) -> str:
        """Return a human-readable description of the task."""

    def prepare_context(self, state: dict) -> dict:
        """Optional: gather/validate context before generation.

        Returns updated state with context included. Default is no-op.
        Override to add research steps, document loading, etc.
        """
        return state

    def validate_context(self, state: dict) -> list[str]:
        """Optional: check that required context is present in state.

        Returns list of validation errors. Empty list means valid.
        """
        return []

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        """Optional: revise output based on judge feedback.

        Returns revised output string. Default returns original (no revision).
        Override to implement LLM-based revision using judge reasoning.
        """
        return output

    def verify_facts(
        self,
        output: str,
        state: dict,
    ) -> dict | None:
        """Optional: verify factual claims in the output.

        Returns a dict with ``verified`` (bool) and ``issues`` (list[str]),
        or ``None`` if no verification is available.  Default returns None.

        **Limitation**: Without an override, hallucination detection relies
        entirely on the LLM judge's training data.  The judge catches obvious
        fabrications but cannot verify claims against external sources.
        Override this method to add external verification (web search, DB
        lookup, etc.) for production use cases involving factual content.
        """
        return None
